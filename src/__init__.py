"""
This file registers the stock-alert model with the Python SDK.
"""

from viam.components.sensor import Sensor
from viam.resource.registry import Registry, ResourceCreatorRegistration
from .stock_alert import StockAlertEmail

# Register the email sensor model
Registry.register_resource_creator(
    Sensor.API,
    StockAlertEmail.MODEL,
    ResourceCreatorRegistration(StockAlertEmail.new, StockAlertEmail.validate_config)
)