import asyncio
from viam.module.module import Module
from viam.components.sensor import Sensor
from viam.logging import getLogger

# Import the StockAlertEmail component
from .alert import StockAlertEmail

LOGGER = getLogger(__name__)

async def main():
    """Initialize and start the module."""
    try:
        LOGGER.info("Starting the stock-alert module")
        
        # Create module from command line arguments
        module = Module.from_args()
        
        # Register the email model
        module.add_model_from_registry(Sensor.API, StockAlertEmail.MODEL)
        
        # Start the module
        await module.start()
    except Exception as e:
        LOGGER.error(f"Failed to start module: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())