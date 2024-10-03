import mysql.connector
from mysql.connector import Error
from db_config import db_config
from datetime import datetime

# Configure logging

with mysql.connector.connect(**db_config) as conn:
    with conn.cursor() as cursor:
        # write code to show all tables in the database
        cursor.execute("SHOW TABLES")
        tables = cursor.fetchall()
        for table in tables:
            print(table)

