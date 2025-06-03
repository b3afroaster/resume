import logging
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
import asyncpg
from datetime import datetime

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Конфигурация БД
DB_CONFIG = {
    "host": "localhost",
    "port": "15432",
    "user": "dima",
    "password": "1234",
    "database": "database"
}

# Инициализация бота
API_TOKEN = '7484482826:AAGlFfIkRXuEG9_ToZzzl-5ZbiUGdtV-ZIs'
bot = Bot(
    token=API_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Состояния для FSM


class Form(StatesGroup):
    user_id = State()
    trial = State()
    condition_score = State()
    drug = State()


async def get_db_connection():
    """Создает соединение с БД"""
    try:
        return await asyncpg.connect(**DB_CONFIG)
    except Exception as e:
        logger.error(f"Ошибка подключения к БД: {str(e)}")
        raise


async def patient_exists(patient_id: int) -> bool:
    """Проверяет существование пациента в БД"""
    try:
        conn = await get_db_connection()
        exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM patients WHERE patient_id = $1)",
            patient_id
        )
        return exists
    except Exception as e:
        logger.error(f"Ошибка при проверке пациента: {str(e)}")
        return False
    finally:
        if 'conn' in locals():
            await conn.close()


async def get_available_trials():
    """Получает список доступных исследований из БД"""
    try:
        conn = await get_db_connection()
        trials = await conn.fetch(
            "SELECT trial_id, trial_name, med FROM trials ORDER BY trial_id"
        )
        return trials if trials else []
    except Exception as e:
        logger.error(f"Ошибка при получении списка исследований: {str(e)}")
        return []
    finally:
        if 'conn' in locals():
            await conn.close()


async def trials_keyboard():
    """Создает клавиатуру с исследованиями из БД"""
    trials = await get_available_trials()
    keyboard_buttons = [
        [KeyboardButton(text=f"{trial['trial_id']}. {trial['trial_name']} ({trial['med']})")]
        for trial in trials
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard_buttons,
        resize_keyboard=True,
        input_field_placeholder="Выберите исследование"
    )


async def drugs_keyboard(trial_id: int):
    """Создает клавиатуру с препаратами для выбранного исследования"""
    try:
        trials = await get_available_trials()
        trial = next((t for t in trials if t['trial_id'] == trial_id), None)

        if not trial:
            logger.error(f"Исследование {trial_id} не найдено")
            return None

        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Плацебо")],
                [KeyboardButton(text=trial['med'])]
            ],
            resize_keyboard=True
        )
    except Exception as e:
        logger.error(f"Ошибка при создании клавиатуры препаратов: {str(e)}")
        return None


async def save_measurement(user_id: int, trial_id: int, drug: str, score: int):
    """Сохраняет измерение в БД"""
    conn = None
    try:
        conn = await get_db_connection()

        # Проверяем существование пациента перед сохранением
        if not await patient_exists(user_id):
            raise ValueError(f"Пациент с ID {user_id} не существует в системе")

        # Проверяем существование исследования
        trial_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM trials WHERE trial_id = $1)",
            trial_id
        )
        if not trial_exists:
            raise ValueError(f"Исследование с ID {trial_id} не существует")

        # Проверяем корректность оценки
        if not 0 <= score <= 100:
            raise ValueError("Оценка самочувствия должна быть от 0 до 100")

        # Сохраняем данные
        await conn.execute(
            """
            INSERT INTO measurements 
            (patient_id, trial_id, measurement_date, drug, condition_score)
            VALUES ($1, $2, $3, $4, $5)
            """,
            user_id, trial_id, datetime.now().date(), drug, score
        )
        logger.info(f"Данные успешно сохранены: user_id={user_id}, trial_id={trial_id}")
        return True
    except asyncpg.PostgresError as e:
        logger.error(f"Ошибка PostgreSQL при сохранении данных: {str(e)}")
        return False
    except Exception as e:
        logger.error(f"Ошибка при сохранении данных: {str(e)}")
        return False
    finally:
        if conn:
            await conn.close()


async def get_drug_statistics(trial_id: int, drug: str):
    """Получает статистику по препарату в исследовании"""
    conn = None
    try:
        conn = await get_db_connection()

        # Получаем среднее значение и количество записей
        result = await conn.fetchrow(
            """
            SELECT 
                AVG(condition_score) as avg_score,
                COUNT(*) as count
            FROM measurements
            WHERE trial_id = $1 AND drug = $2
            """,
            trial_id, drug
        )

        if not result or result['count'] == 0:
            return None

        avg_score = round(float(result['avg_score']), 1)
        count = result['count']

        # Рассчитываем диапазон нормы (±10%)
        lower_bound = round(avg_score * 0.9, 1)
        upper_bound = round(avg_score * 1.1, 1)

        return {
            'avg_score': avg_score,
            'count': count,
            'lower_bound': lower_bound,
            'upper_bound': upper_bound
        }
    except Exception as e:
        logger.error(f"Ошибка при получении статистики: {str(e)}")
        return None
    finally:
        if conn:
            await conn.close()


# Стартовый обработчик
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.set_state(Form.user_id)
    await message.answer(
        "Добро пожаловать в систему клинических исследований!\n"
        "Пожалуйста, введите ваш ID пациента (должен существовать в системе):",
        reply_markup=types.ReplyKeyboardRemove()
    )


# Обработка ID пользователя
@dp.message(Form.user_id)
async def process_user_id(message: types.Message, state: FSMContext):
    try:
        user_input = message.text.strip()
        if not user_input.isdigit():
            raise ValueError("ID должен содержать только цифры")

        user_id = int(user_input)
        if user_id <= 0:
            raise ValueError("ID должен быть положительным")

        # Проверяем существование пациента
        if not await patient_exists(user_id):
            raise ValueError(f"Пациент с ID {user_id} не зарегистрирован в системе")

        await state.update_data(user_id=user_id)
        await state.set_state(Form.trial)
        await message.answer(
            "Выберите исследование:",
            reply_markup=await trials_keyboard()
        )
    except ValueError as e:
        await message.answer(
            f"Ошибка: {str(e)}. Пожалуйста, введите корректный ID пациента:",
            reply_markup=types.ReplyKeyboardRemove()
        )


# Обработка выбора исследования
@dp.message(Form.trial)
async def process_trial(message: types.Message, state: FSMContext):
    try:
        trial_id = int(message.text.split(".")[0])
        trials = await get_available_trials()
        trial = next((t for t in trials if t['trial_id'] == trial_id), None)

        if not trial:
            raise ValueError

        await state.update_data(
            trial_id=trial_id,
            trial_name=trial['trial_name'],
            trial_med=trial['med']
        )
        await state.set_state(Form.condition_score)
        await message.answer(
            "Введите оценку вашего самочувствия (0-100):",
            reply_markup=types.ReplyKeyboardRemove()
        )
    except (ValueError, IndexError, AttributeError):
        await message.answer(
            "Пожалуйста, выберите исследование из списка:",
            reply_markup=await trials_keyboard()
        )


# Обработка оценки самочувствия
@dp.message(Form.condition_score)
async def process_condition_score(message: types.Message, state: FSMContext):
    try:
        score = int(message.text.strip())
        if not 0 <= score <= 100:
            raise ValueError

        await state.update_data(condition_score=score)
        user_data = await state.get_data()

        await state.set_state(Form.drug)
        await message.answer(
            "Выберите принимаемый препарат:",
            reply_markup=await drugs_keyboard(user_data['trial_id'])
        )
    except ValueError:
        await message.answer(
            "Оценка должна быть целым числом от 0 до 100. Пожалуйста, введите корректное значение:",
            reply_markup=types.ReplyKeyboardRemove()
        )


# Обработка выбора препарата
@dp.message(Form.drug)
async def process_drug(message: types.Message, state: FSMContext):
    drug = message.text
    user_data = await state.get_data()

    # Проверка что препарат относится к исследованию или это плацебо
    valid_drugs = ["Плацебо", user_data.get('trial_med', '')]
    if drug not in valid_drugs:
        await message.answer(
            f"Пожалуйста, выберите препарат для исследования {user_data['trial_name']}:\n"
            f"Доступные варианты: Плацебо или {user_data['trial_med']}",
            reply_markup=await drugs_keyboard(user_data['trial_id'])
        )
        return

    # Сохранение данных с обработкой ошибок
    try:
        success = await save_measurement(
            user_data['user_id'],
            user_data['trial_id'],
            drug,
            user_data['condition_score']
        )

        if not success:
            raise ValueError("Ошибка при сохранении данных в базу")

        # Получаем статистику по препарату
        stats = await get_drug_statistics(user_data['trial_id'], drug)
        score = user_data["condition_score"]

        if stats:
            # Анализ данных на основе статистики
            within_normal = stats['lower_bound'] <= score <= stats['upper_bound']
            analysis = (
                "Ваше самочувствие в пределах нормы.\n"
                f"Среднее значение для препарата {drug}: {stats['avg_score']}\n"
                f"Диапазон нормы: от {stats['lower_bound']} до {stats['upper_bound']}\n"
                f"На основе {stats['count']} измерений"
            ) if within_normal else (
                "Ваше самочувствие выходит за пределы нормы.\n"
                f"Среднее значение для препарата {drug}: {stats['avg_score']}\n"
                f"Диапазон нормы: от {stats['lower_bound']} до {stats['upper_bound']}\n"
                f"На основе {stats['count']} измерений"
            )
        else:
            # Если статистики нет (первое измерение)
            analysis = (
                "Это первое измерение для данного препарата в исследовании.\n"
                "Нормальный диапазон будет определен после сбора большего количества данных."
            )

        await message.answer(
            "Спасибо за предоставленную информацию!\n"
            f"ID: {user_data['user_id']}\n"
            f"Исследование: {user_data['trial_name']}\n"
            f"Самочувствие: {score}/100\n"
            f"Препарат: {drug}\n\n"
            f"{analysis}",
            reply_markup=types.ReplyKeyboardRemove()
        )
    except Exception as e:
        logger.error(f"Ошибка в процессе сохранения данных: {str(e)}")
        await message.answer(
            f"Произошла ошибка при сохранении данных: {str(e)}\n"
            "Пожалуйста, попробуйте позже или начните заново /start",
            reply_markup=types.ReplyKeyboardRemove()
        )
    finally:
        await state.clear()


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())