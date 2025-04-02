"""
This file registers the stock-alert model with the Python SDK.
"""

from viam.components.sensor import Sensor
from viam.resource.registry import Registry, ResourceCreatorRegistration
from .main import StockAlertEmail

# Register the email sensor model
Registry.register_resource_creator(
    Sensor.API,
    StockAlertEmail.MODEL,
    ResourceCreatorRegistration(StockAlertEmail.new, StockAlertEmail.validate_config)
)

# Register the SMS sensor model
Registry.register_resource_creator(
    Sensor.API,
    StockAlertSMS.MODEL,
    ResourceCreatorRegistration(StockAlertSMS.new, StockAlertSMS.validate_config)
)