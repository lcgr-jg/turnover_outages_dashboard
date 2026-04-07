import os


try:
    # Try and import from local login.py file
    from login import snowflake_login
    
except Exception:
    # Otherwise we read from server env vars

    snowflake_login = {'account': os.getenv('SNOWFLAKE_ACCOUNT'),
                       'username': os.getenv('SNOWFLAKE_USR'),
                       'keyfilepass': os.getenv('SNOWFLAKE_KEYFILE_PASS'),
                       }
    
