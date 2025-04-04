#!/usr/bin/env python3
import asyncio
import os
from typing import Mapping, Optional, Any
from viam.module.module import Module
from viam.components.sensor import Sensor
from viam.services.generic import Generic
from viam.proto.app.robot import ComponentConfig
from viam.resource.base import ResourceBase
from viam.resource.types import Model, ModelFamily
from viam.utils import SensorReading, struct_to_dict
from viam.logging import getLogger

# Import your models
from .alert import BaseStockAlert, StockAlertEmail

LOGGER = getLogger(__name__)

async def main():
    """Initialize and start the module."""
    try:
        LOGGER.info("Starting the stock-alert module")
        LOGGER.info(f"Current working directory: {os.getcwd()}")
        LOGGER.info(f"Process ID: {os.getpid()}")
        
        module = Module.from_args()
        
        # Register only the email model for now
        module.add_model_from_registry(Sensor.API, StockAlertEmail.MODEL)
        LOGGER.info(f"Registered model: {StockAlertEmail.MODEL}")
        
        # Start the module
        LOGGER.info("Starting module...")
        await module.start()
    except Exception as e:
        LOGGER.error(f"Failed to start module: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())