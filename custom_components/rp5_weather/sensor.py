"""Diagnostic timestamp for the shared RP5 refresh."""
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from homeassistant.helpers.device_registry import DeviceInfo, DeviceEntryType
from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([RP5Updated(entry.runtime_data, entry)])


class RP5Updated(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_name = 'Данные получены'
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = f'{entry.unique_id}_fetched_at'
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.unique_id)}, name=entry.title,
                                           entry_type=DeviceEntryType.SERVICE,
                                           configuration_url=entry.data['url'], manufacturer='RP5')

    @property
    def native_value(self):
        from datetime import datetime
        return datetime.fromisoformat(self.coordinator.data['fetched_at'])
