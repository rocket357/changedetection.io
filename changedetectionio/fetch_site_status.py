import hashlib
import logging
import os
import re
import time
import urllib3

from changedetectionio import content_fetcher, html_tools

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# Some common stuff here that can be moved to a base class
class perform_site_check():

    def __init__(self, *args, datastore, **kwargs):
        super().__init__(*args, **kwargs)
        self.datastore = datastore

    # If there was a proxy list enabled, figure out what proxy_args/which proxy to use
    # if watch.proxy use that
    # fetcher.proxy_override = watch.proxy or main config proxy
    # Allows override the proxy on a per-request basis
    # ALWAYS use the first one is nothing selected

    def set_proxy_from_list(self, watch):
        proxy_args = None
        if self.datastore.proxy_list is None:
            return None

        # If its a valid one
        if any([watch['proxy'] in p for p in self.datastore.proxy_list]):
            proxy_args = watch['proxy']

        # not valid (including None), try the system one
        else:
            system_proxy = self.datastore.data['settings']['requests']['proxy']
            # Is not None and exists
            if any([system_proxy in p for p in self.datastore.proxy_list]):
                proxy_args = system_proxy

        # Fallback - Did not resolve anything, use the first available
        if proxy_args is None:
            proxy_args = self.datastore.proxy_list[0][0]

        return proxy_args

    def run(self, uuid):
        timestamp = int(time.time())  # used for storage etc too

        changed_detected = False
        screenshot = False  # as bytes
        stripped_text_from_html = ""

        watch = self.datastore.data['watching'][uuid]

        # Protect against file:// access
        if re.search(r'^file', watch['url'], re.IGNORECASE) and not os.getenv('ALLOW_FILE_URI', False):
            raise Exception(
                "file:// type access is denied for security reasons."
            )

        # Unset any existing notification error
        update_obj = {'last_notification_error': False, 'last_error': False}

        extra_headers = self.datastore.get_val(uuid, 'headers')

        # Tweak the base config with the per-watch ones
        request_headers = self.datastore.data['settings']['headers'].copy()
        request_headers.update(extra_headers)

        # https://github.com/psf/requests/issues/4525
        # Requests doesnt yet support brotli encoding, so don't put 'br' here, be totally sure that the user cannot
        # do this by accident.
        if 'Accept-Encoding' in request_headers and "br" in request_headers['Accept-Encoding']:
            request_headers['Accept-Encoding'] = request_headers['Accept-Encoding'].replace(', br', '')

        timeout = self.datastore.data['settings']['requests']['timeout']
        url = self.datastore.get_val(uuid, 'url')
        request_body = self.datastore.get_val(uuid, 'body')
        request_method = self.datastore.get_val(uuid, 'method')
        ignore_status_code = self.datastore.get_val(uuid, 'ignore_status_codes')

        # source: support
        is_source = False
        if url.startswith('source:'):
            url = url.replace('source:', '')
            is_source = True

        # Pluggable content fetcher
        prefer_backend = watch['fetch_backend']
        if hasattr(content_fetcher, prefer_backend):
            klass = getattr(content_fetcher, prefer_backend)
        else:
            # If the klass doesnt exist, just use a default
            klass = getattr(content_fetcher, "html_requests")


        proxy_args = self.set_proxy_from_list(watch)
        fetcher = klass(proxy_override=proxy_args)

        # Configurable per-watch or global extra delay before extracting text (for webDriver types)
        system_webdriver_delay = self.datastore.data['settings']['application'].get('webdriver_delay', None)
        if watch['webdriver_delay'] is not None:
            fetcher.render_extract_delay = watch['webdriver_delay']
        elif system_webdriver_delay is not None:
            fetcher.render_extract_delay = system_webdriver_delay

        if watch['webdriver_js_execute_code'] is not None and watch['webdriver_js_execute_code'].strip():
            fetcher.webdriver_js_execute_code = watch['webdriver_js_execute_code']

        fetcher.run(url, timeout, request_headers, request_body, request_method, ignore_status_code, watch['css_filter'])
        fetcher.quit()

        # Fetching complete, now filters
        # @todo move to class / maybe inside of fetcher abstract base?

        # @note: I feel like the following should be in a more obvious chain system
        #  - Check filter text
        #  - Is the checksum different?
        #  - Do we convert to JSON?
        # https://stackoverflow.com/questions/41817578/basic-method-chaining ?
        # return content().textfilter().jsonextract().checksumcompare() ?

        is_json = 'application/json' in fetcher.headers.get('Content-Type', '')
        is_html = not is_json

        # source: support, basically treat it as plaintext
        if is_source:
            is_html = False
            is_json = False

        css_filter_rule = watch['css_filter']
        subtractive_selectors = watch.get(
            "subtractive_selectors", []
        ) + self.datastore.data["settings"]["application"].get(
            "global_subtractive_selectors", []
        )

        has_filter_rule = css_filter_rule and len(css_filter_rule.strip())
        has_subtractive_selectors = subtractive_selectors and len(subtractive_selectors[0].strip())

        if is_json and not has_filter_rule:
            css_filter_rule = "json:$"
            has_filter_rule = True

        if has_filter_rule:
            if 'json:' in css_filter_rule:
                stripped_text_from_html = html_tools.extract_json_as_string(content=fetcher.content, jsonpath_filter=css_filter_rule)
                is_html = False

        if is_html or is_source:
            
            # CSS Filter, extract the HTML that matches and feed that into the existing inscriptis::get_text
            fetcher.content = html_tools.workarounds_for_obfuscations(fetcher.content)
            html_content = fetcher.content

            # If not JSON,  and if it's not text/plain..
            if 'text/plain' in fetcher.headers.get('Content-Type', '').lower():
                # Don't run get_text or xpath/css filters on plaintext
                stripped_text_from_html = html_content
            else:
                # Then we assume HTML
                if has_filter_rule:
                    # For HTML/XML we offer xpath as an option, just start a regular xPath "/.."
                    if css_filter_rule[0] == '/' or css_filter_rule.startswith('xpath:'):
                        html_content = html_tools.xpath_filter(xpath_filter=css_filter_rule.replace('xpath:', ''),
                                                               html_content=fetcher.content)
                    else:
                        # CSS Filter, extract the HTML that matches and feed that into the existing inscriptis::get_text
                        html_content = html_tools.css_filter(css_filter=css_filter_rule, html_content=fetcher.content)

                if has_subtractive_selectors:
                    html_content = html_tools.element_removal(subtractive_selectors, html_content)

                if not is_source:
                    # extract text
                    stripped_text_from_html = \
                        html_tools.html_to_text(
                            html_content,
                            render_anchor_tag_content=self.datastore.data["settings"][
                                "application"].get(
                                "render_anchor_tag_content", False)
                        )

                elif is_source:
                    stripped_text_from_html = html_content

            # Re #340 - return the content before the 'ignore text' was applied
            text_content_before_ignored_filter = stripped_text_from_html.encode('utf-8')

        # Re #340 - return the content before the 'ignore text' was applied
        text_content_before_ignored_filter = stripped_text_from_html.encode('utf-8')

        # Treat pages with no renderable text content as a change? No by default
        empty_pages_are_a_change = self.datastore.data['settings']['application'].get('empty_pages_are_a_change', False)
        if not is_json and not empty_pages_are_a_change and len(stripped_text_from_html.strip()) == 0:
            raise content_fetcher.ReplyWithContentButNoText(url=url, status_code=200)

        # We rely on the actual text in the html output.. many sites have random script vars etc,
        # in the future we'll implement other mechanisms.

        update_obj["last_check_status"] = fetcher.get_last_status_code()

        # If there's text to skip
        # @todo we could abstract out the get_text() to handle this cleaner
        text_to_ignore = watch.get('ignore_text', []) + self.datastore.data['settings']['application'].get('global_ignore_text', [])
        if len(text_to_ignore):
            stripped_text_from_html = html_tools.strip_ignore_text(stripped_text_from_html, text_to_ignore)
        else:
            stripped_text_from_html = stripped_text_from_html.encode('utf8')

        # 615 Extract text by regex
        extract_text = watch.get('extract_text', [])
        if len(extract_text) > 0:
            regex_matched_output = []
            for s_re in extract_text:
                result = re.findall(s_re.encode('utf8'), stripped_text_from_html,
                                    flags=re.MULTILINE | re.DOTALL | re.LOCALE)
                if result:
                    regex_matched_output = regex_matched_output + result

            if regex_matched_output:
                stripped_text_from_html = b'\n'.join(regex_matched_output)
                text_content_before_ignored_filter = stripped_text_from_html

        # Re #133 - if we should strip whitespaces from triggering the change detected comparison
        if self.datastore.data['settings']['application'].get('ignore_whitespace', False):
            fetched_md5 = hashlib.md5(stripped_text_from_html.translate(None, b'\r\n\t ')).hexdigest()
        else:
            fetched_md5 = hashlib.md5(stripped_text_from_html).hexdigest()

        ############ Blocking rules, after checksum #################
        blocked = False

        if len(watch['trigger_text']):
            # Assume blocked
            blocked = True
            # Filter and trigger works the same, so reuse it
            # It should return the line numbers that match
            result = html_tools.strip_ignore_text(content=str(stripped_text_from_html),
                                                  wordlist=watch['trigger_text'],
                                                  mode="line numbers")
            # Unblock if the trigger was found
            if result:
                blocked = False


        if len(watch['text_should_not_be_present']):
            # If anything matched, then we should block a change from happening
            result = html_tools.strip_ignore_text(content=str(stripped_text_from_html),
                                                  wordlist=watch['text_should_not_be_present'],
                                                  mode="line numbers")
            if result:
                blocked = True

        # The main thing that all this at the moment comes down to :)
        if watch['previous_md5'] != fetched_md5:
            changed_detected = True

        # Looks like something changed, but did it match all the rules?
        if blocked:
            changed_detected = False

        # Extract title as title
        if is_html:
            if self.datastore.data['settings']['application']['extract_title_as_title'] or watch['extract_title_as_title']:
                if not watch['title'] or not len(watch['title']):
                    update_obj['title'] = html_tools.extract_element(find='title', html_content=fetcher.content)

        if changed_detected:
            if watch.get('check_unique_lines', False):
                has_unique_lines = watch.lines_contain_something_unique_compared_to_history(lines=stripped_text_from_html.splitlines())
                # One or more lines? unsure?
                if not has_unique_lines:
                    logging.debug("check_unique_lines: UUID {} didnt have anything new setting change_detected=False".format(uuid))
                    changed_detected = False
                else:
                    logging.debug("check_unique_lines: UUID {} had unique content".format(uuid))

        # Always record the new checksum
        update_obj["previous_md5"] = fetched_md5

        # On the first run of a site, watch['previous_md5'] will be None, set it the current one.
        if not watch.get('previous_md5'):
            watch['previous_md5'] = fetched_md5

        return changed_detected, update_obj, text_content_before_ignored_filter, fetcher.screenshot, fetcher.xpath_data
