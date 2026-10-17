"""Observed conditions and forecast from RP5, with shared coordinator cache."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from homeassistant.components.weather import WeatherEntity, WeatherEntityFeature
from homeassistant.const import UnitOfTemperature, UnitOfPressure, UnitOfSpeed, UnitOfPrecipitationDepth
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo, DeviceEntryType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([RP5Weather(entry.runtime_data, entry)])


class RP5Weather(CoordinatorEntity, WeatherEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_attribution = 'Данные предоставлены rp5.ru'
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_pressure_unit = UnitOfPressure.HPA
    _attr_native_wind_speed_unit = UnitOfSpeed.METERS_PER_SECOND
    _attr_native_precipitation_unit = UnitOfPrecipitationDepth.MILLIMETERS
    _attr_supported_features = WeatherEntityFeature.FORECAST_HOURLY

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = f'{entry.unique_id}_weather'
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.unique_id)}, name=entry.title,
                                           entry_type=DeviceEntryType.SERVICE,
                                           configuration_url=entry.data['url'], manufacturer='RP5')

    @property
    def current(self):
        data = self.coordinator.data['observation']
        if data and datetime.now(timezone.utc) - datetime.fromisoformat(data['observed_at_approx']) <= timedelta(hours=6):
            return data
        return {}

    @property
    def condition(self):
        return self.current.get('condition')

    @property
    def native_temperature(self):
        return self.current.get('native_temperature')

    @property
    def native_wind_speed(self):
        return self.current.get('native_wind_speed')

    @property
    def wind_bearing(self):
        return self.current.get('wind_bearing')

    @property
    def extra_state_attributes(self):
        return {'observation_available': bool(self.current),
                'forecast_available': bool(self.coordinator.data['forecast']),
                'forecast_error': self.coordinator.data.get('forecast_error'),
                'observation_error': self.coordinator.data.get('observation_error'),
                'source_url': self.coordinator.data['source_url'],
                'data_fetched_at': self.coordinator.data['fetched_at'],
                'observation_time_approx': self.current.get('observed_at_approx'),
                'observation_description': self.current.get('description'),
                'observation_reported_age_minutes': self.current.get('reported_age_minutes'),
                'forecast_interval_note': '1 час в начале прогноза, затем 3 часа; без интерполяции'}

    async def async_forecast_hourly(self):
        # Return source slots, including the documented later 3-hour steps.
        # Reading the forecast never triggers a network request.
        now = datetime.now(timezone.utc)
        return [f for f in self.coordinator.data['forecast'] if datetime.fromisoformat(f['datetime']) >= now]

    @callback
    def _handle_coordinator_update(self):
        super()._handle_coordinator_update()
        self.hass.async_create_task(self.async_update_listeners(None))
