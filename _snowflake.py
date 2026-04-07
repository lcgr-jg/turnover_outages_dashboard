import os
import snowflake.connector

# Generic class inherited by other useful queries (eg Platts and Bloomberg)

# Dumping some relevant/useful links here:
     # How to find out account identifier: https://docs.snowflake.com/en/user-guide/admin-account-identifier
     # Python connector: https://docs.snowflake.com/en/developer-guide/python-connector/python-connector-install
     # Required package: pip install "snowflake-connector-python[pandas]"


class SnowFlake():
    
    # Hard coded key file name (this file is saved within the corelib package)
    _keyfile = 'rsa_key.p8'
    _login = None
    _info = ''
    
    
    def __init__(self, database, schema, login={}, role='JG_GENERAL', warehouse='EU_COMMOD_RESEARCH_XS_WH'):
        
        self._login = login
        
        if isinstance(self._login, dict) and (len(self._login)==0):
            try:
                from _credentials import snowflake_login
                
                self._login = snowflake_login
            except Exception:
                msg = "Failed to find credentials"
                raise Exception(msg)
                
        self.validate_login(self._login)
        
        self._account = self._login.get('account')
        self._username = self._login.get('username')
        self._keyfilepass = self._login.get('keyfilepass')
        
        # NB: these must both be set when instantiating the class as they depend on what is to be queried
        self._database = database
        self._schema = schema
        
        # On a similiar note, role seems to be usually JG_GENERAL but is set as an optional param in case of testing etc
        self._role = role
        
        # To avoid using the default dev/test warehouse, Pulkit advised to use one of the following to improve speed of large queries. Available option: 
            # EU_COMMOD_RESEARCH_XS_WH
            # EU_COMMOD_RESEARCH_S_WH
            # EU_COMMOD_RESEARCH_M_WH
        self._warehouse = warehouse
        
        self._conn = self.get_conn()
        self._info = self.get_info()

        
    def validate_login(self, login):
        
        if not isinstance(login, dict):
            msg = f'login must be provided as a dict, got type {type(login)}'
            raise Exception(msg)
            
        if 'account' not in login.keys():
            msg = f"login keys must included 'account', got {login.keys()}"
            raise Exception(msg)
            
        if 'username' not in login.keys():
            msg = f"login keys must included 'username', got {login.keys()}"            
            raise Exception(msg)        
            
        if 'keyfilepass' not in login.keys():
            msg = f"login keys must included 'keyfilepass', got {login.keys()}"            
            raise Exception(msg) 
            
            
    def get_info(self):
        info = f"""
                SnowFlake instance config:
                ======================
                
                connection: {self._conn}
                """
        return info
    
    
    def get_conn(self):
        
        # Old connection logic for an individual user account (ie just username & password)
        
        # conn = snowflake.connector.connect(user=self._username,
        #                                    password=self._password,
        #                                    account=self._account,
        #                                    database=self._database,
        #                                    schema=self._schema,
        #                                    )
                
        # Snowflake service accounts also use private/public key for connection
        # reference: https://docs.snowflake.com/en/developer-guide/python-connector/python-connector-connect#label-python-key-pair-authn-rotation
        
        # Keyfile is saved within corelib so we need to create the full path
        keyfile_path = os.path.join(os.path.dirname(__file__), self._keyfile)
        
        # Create dict & connect
        conn_details = {'user': self._username,
                        'private_key_file': keyfile_path,
                        'private_key_file_pwd': self._keyfilepass,
                        'account': self._account,
                        'database': self._database,
                        'schema': self._schema,
                        'role': self._role, # see note at top
                        'warehouse': self._warehouse,
                        }
        conn = snowflake.connector.connect(**conn_details)
        
        return conn


        
    @property
    def engine(self):
        return self._engine
    
    @property
    def login(self):
        return self._login    
    
    def __repr__(self):
        return self._info






    




