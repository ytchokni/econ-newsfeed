import os
from dotenv import load_dotenv

load_dotenv()

# AWS RDS MySQL configuration
db_config = {
    'host': os.environ.get('DB_HOST'),
    'user': os.environ.get('DB_USER'),
    'password': os.environ.get('DB_PASSWORD'),
    'database': os.environ.get('DB_NAME'),
}

# OpenAI API configuration
openai_config = {
    'api_key': os.environ.get('OPENAI_API_KEY')
}


print(db_config)
