from viam.components.sensor import Sensor
from viam.resource.registry import Registry, ResourceCreatorRegistration
from viam.logging import getLogger
from .alert import StockAlertEmail

LOGGER = getLogger(__name__)

# Register the email sensor model
LOGGER.info("Registering StockAlertEmail model")
Registry.register_resource_creator(
    Sensor.API,
    StockAlertEmail.MODEL,
    ResourceCreatorRegistration(StockAlertEmail.new, StockAlertEmail.validate_config)
)
LOGGER.info(f"Registered model: {StockAlertEmail.MODEL}")