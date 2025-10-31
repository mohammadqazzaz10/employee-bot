-- Employees
CREATE TABLE IF NOT EXISTS employees (
  id SERIAL PRIMARY KEY,
  telegram_id BIGINT UNIQUE,
  phone_number VARCHAR(20) UNIQUE NOT NULL,
  full_name VARCHAR(100) NOT NULL,
  last_active TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Daily cigarettes counter
CREATE TABLE IF NOT EXISTS daily_cigarettes (
  employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
  date        DATE NOT NULL,
  count       INTEGER NOT NULL DEFAULT 0,
  updated_at  TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (employee_id, date)
);

-- Lunch breaks
CREATE TABLE IF NOT EXISTS lunch_breaks (
  id SERIAL PRIMARY KEY,
  employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
  date DATE NOT NULL,
  taken BOOLEAN DEFAULT FALSE,
  taken_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(employee_id, date)
);

-- Cigarette times
CREATE TABLE IF NOT EXISTS cigarette_times (
  id SERIAL PRIMARY KEY,
  employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
  taken_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Attendance
CREATE TABLE IF NOT EXISTS attendance (
  id SERIAL PRIMARY KEY,
  employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
  date DATE NOT NULL,
  check_in_time TIMESTAMPTZ,
  check_out_time TIMESTAMPTZ,
  is_late BOOLEAN DEFAULT FALSE,
  late_minutes INTEGER DEFAULT 0,
  total_work_hours DECIMAL(5,2) DEFAULT 0,
  overtime_hours DECIMAL(5,2) DEFAULT 0,
  status VARCHAR(20) DEFAULT 'present',
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(employee_id, date)
);

-- Warnings
CREATE TABLE IF NOT EXISTS warnings (
  id SERIAL PRIMARY KEY,
  employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
  warning_type VARCHAR(50) NOT NULL,
  warning_reason TEXT NOT NULL,
  date DATE NOT NULL,
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Absences
CREATE TABLE IF NOT EXISTS absences (
  id SERIAL PRIMARY KEY,
  employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
  date DATE NOT NULL,
  absence_type VARCHAR(50) NOT NULL,
  reason TEXT,
  excuse TEXT,
  is_excused BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(employee_id, date)
);