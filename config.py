import os
from dotenv import load_dotenv

load_dotenv()

# إعدادات البوت
BOT_TOKEN = os.getenv('BOT_TOKEN')
SUPER_ADMIN_IDS = [int(x) for x in os.getenv('SUPER_ADMIN_IDS', '').split(',') if x]

# إعدادات قاعدة البيانات
DATABASE_URL = os.getenv('DATABASE_URL')

# إعدادات الوقت
TIMEZONE = 'Asia/Amman'

# إعدادات العمل
WORK_START_TIME = "08:00"
WORK_END_TIME = "19:00"
LATE_TOLERANCE_MINUTES = 15
LUNCH_BREAK_DURATION = 30  # دقائق
SMOKE_BREAK_DURATION = 10  # دقائق
MIN_SMOKE_INTERVAL = 1.5 * 60 * 60  # 1.5 ساعة بالثواني

# أيام العمل
WORK_DAYS = [0, 1, 2, 3, 4, 5]  # الإثنين إلى السبت
FRIDAY = 5  # الجمعة هو يوم عمل إضافي