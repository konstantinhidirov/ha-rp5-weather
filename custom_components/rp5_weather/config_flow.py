"""Find an RP5 location by name and choose its forecast page."""
from __future__ import annotations

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import selector

from .client import RP5Client
from .const import CONF_INTERVAL, CONF_URL, DEFAULT_INTERVAL, DOMAIN
from .parser import ParseError
from .search import normalize_query, search_locations

CONF_QUERY = 'query'
CONF_LOCATION = 'location'
SEARCH_AGAIN = '__search_again__'

SEARCH_SCHEMA = vol.Schema({vol.Required(CONF_QUERY): selector.TextSelector()})

URL_SCHEMA = vol.Schema({vol.Required(CONF_URL): selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.URL))})


class RP5ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        super().__init__()
        self._query = ''
        self._locations = {}

    async def _async_create_location(self, url, errors, error_field=CONF_QUERY):
        try:
            client = RP5Client(async_get_clientsession(self.hass), url)
            data = await client.fetch()
        except (ParseError, aiohttp.ClientError, TimeoutError):
            errors['base'] = 'cannot_connect'
        except ValueError:
            errors[error_field] = 'invalid_url'
        else:
            await self.async_set_unique_id(data['location']['point_id'])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=data['location']['name'], data={CONF_URL: client.url})
        return None

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            self._locations = {}
            # Accept pasted URLs too, including input from the previous form.
            query = user_input.get(CONF_QUERY, user_input.get(CONF_URL, ''))
            self._query = query.strip() if isinstance(query, str) else ''
            if self._query.lower().startswith(('http://', 'https://')):
                result = await self._async_create_location(self._query, errors)
                if result is not None:
                    return result
            else:
                try:
                    self._query = normalize_query(query)
                    locations = await search_locations(async_get_clientsession(self.hass), self._query)
                except (ParseError, aiohttp.ClientError, TimeoutError):
                    errors['base'] = 'cannot_search'
                except ValueError:
                    errors[CONF_QUERY] = 'invalid_query'
                else:
                    self._locations = {item['url']: item['label'] for item in locations}
                    if self._locations:
                        return await self.async_step_select_location()
                    errors[CONF_QUERY] = 'no_results'
        schema = self.add_suggested_values_to_schema(SEARCH_SCHEMA, {CONF_QUERY: self._query})
        return self.async_show_form(step_id='user', data_schema=schema, errors=errors)

    async def async_step_select_location(self, user_input=None):
        if not self._locations:
            return await self.async_step_user()
        errors = {}
        if user_input is not None:
            url = user_input.get(CONF_LOCATION)
            if url == SEARCH_AGAIN:
                self._locations = {}
                return await self.async_step_user()
            if url not in self._locations:
                errors[CONF_LOCATION] = 'invalid_location'
            else:
                result = await self._async_create_location(url, errors, CONF_LOCATION)
                if result is not None:
                    return result
        options = [{'value': url, 'label': label} for url, label in self._locations.items()]
        options.append({'value': SEARCH_AGAIN, 'label': 'Искать другое название…'})
        schema = vol.Schema({vol.Required(CONF_LOCATION): selector.SelectSelector(
            selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.DROPDOWN))})
        return self.async_show_form(step_id='select_location', data_schema=schema, errors=errors,
                                    description_placeholders={'query': self._query})

    async def async_step_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            try:
                client = RP5Client(async_get_clientsession(self.hass), user_input[CONF_URL])
                data = await client.fetch()
            except (ParseError, aiohttp.ClientError, TimeoutError):
                errors['base'] = 'cannot_connect'
            except ValueError:
                errors[CONF_URL] = 'invalid_url'
            else:
                # A different location should get its own stable entity IDs.
                if data['location']['point_id'] != entry.unique_id:
                    errors['base'] = 'different_location'
                else:
                    return self.async_update_reload_and_abort(entry, data_updates={CONF_URL: client.url})
        schema = self.add_suggested_values_to_schema(URL_SCHEMA, {CONF_URL: entry.data[CONF_URL]})
        return self.async_show_form(step_id='reconfigure', data_schema=schema, errors=errors)

    @staticmethod
    def async_get_options_flow(config_entry):
        return RP5OptionsFlow()


class RP5OptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title='', data=user_input)
        schema = vol.Schema({vol.Required(CONF_INTERVAL, default=self.config_entry.options.get(CONF_INTERVAL, DEFAULT_INTERVAL)):
                             vol.All(vol.Coerce(int), vol.Range(min=60, max=720))})
        return self.async_show_form(step_id='init', data_schema=schema)
