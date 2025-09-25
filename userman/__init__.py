""" Userman: Simple account handling system for use with web services.

This implementation uses Tornado (3.2 or later) and ibmcloudant (0.10.7 or later).
"""

__version__ = '14.8.0'

# These values are minimal default values appropriate for debugging.
# The actual values are read in by utils.load_settings()
settings = dict(BASE_URL='http://localhost:8880/',
                DB_SERVER='http://localhost:5984/',
                DB_DATABASE='userman',
                TORNADO_DEBUG=True,
                LOGGING_DEBUG=True,
                LOGGING_FORMAT='%(levelname)s [%(asctime)s] %(message)s',
                API_KEYS=[],
                ACTIVATION_EMAIL='messages/activation_email.txt',
                RESET_EMAIL='messages/reset_email.txt',
                ACTIVATION_PERIOD=7.0, # Unit: days
                )
