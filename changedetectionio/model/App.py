import collections
import os

import uuid as uuid_builder

from changedetectionio.notification import (
    default_notification_body,
    default_notification_format,
    default_notification_title,
)

class model(dict):
    base_config = {
            'note': "Hello! If you change this file manually, please be sure to restart your changedetection.io instance!",
            'watching': {},
            'settings': {
                'headers': {
                    'User-Agent': os.getenv("DEFAULT_SETTINGS_HEADERS_USERAGENT", 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.66 Safari/537.36'),
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
                    'Accept-Encoding': 'gzip, deflate',  # No support for brolti in python requests yet.
                    'Accept-Language': 'en-GB,en-US;q=0.9,en;'
                },
                'requests': {
                    'timeout': int(os.getenv("DEFAULT_SETTINGS_REQUESTS_TIMEOUT", "45")),  # Default 45 seconds
                    'time_between_check': {'weeks': None, 'days': None, 'hours': 3, 'minutes': None, 'seconds': None},
                    'jitter_seconds': 0,
                    'workers': int(os.getenv("DEFAULT_SETTINGS_REQUESTS_WORKERS", "10")),  # Number of threads, lower is better for slow connections
                    'proxy': None # Preferred proxy connection
                },
                'application': {
                    'api_access_token_enabled': True,
                    'password': False,
                    'base_url' : None,
                    'extract_title_as_title': False,
                    'empty_pages_are_a_change': False,
                    'fetch_backend': os.getenv("DEFAULT_FETCH_BACKEND", "html_requests"),
                    'global_ignore_text': [], # List of text to ignore when calculating the comparison checksum
                    'global_subtractive_selectors': [],
                    'ignore_whitespace': True,
                    'render_anchor_tag_content': False,
                    'notification_urls': [], # Apprise URL list
                    # Custom notification content
                    'notification_title': default_notification_title,
                    'notification_body': default_notification_body,
                    'notification_format': default_notification_format,
                    'real_browser_save_screenshot': True,
                    'schema_version' : 0,
                    'webdriver_delay': None  # Extra delay in seconds before extracting text
                }
            }
        }

    def __init__(self, *arg, **kw):
        super(model, self).__init__(*arg, **kw)
        self.update(self.base_config)
