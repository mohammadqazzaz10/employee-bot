import os
import psycopg2

DDL = open('schema.sql','r',encoding='utf-8').read()

def main():
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        raise SystemExit('DATABASE_URL is missing')
    if 'sslmode=' not in database_url:
        database_url += ('&' if '?' in database_url else '?') + 'sslmode=require'

    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
    print('✅ All tables created or already exist.')

if __name__ == '__main__':
    main()