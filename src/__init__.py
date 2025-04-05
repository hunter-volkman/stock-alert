from viam.components.sensor import Sensor
from viam.resource.registry import Registry, ResourceCreatorRegistration

# Import the model
from .alert import StockAlertEmail

# Register the model
Registry.register_resource_creator(
    Sensor.API,
    StockAlertEmail.MODEL,
    ResourceCreatorRegistration(StockAlertEmail.new, StockAlertEmail.validate_config)
)