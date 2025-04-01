#!/usr/bin/env python3
import asyncio
from typing import Mapping, Optional, Any
from viam.module.module import Module
from viam.components.sensor import Sensor
from viam.services.generic import Generic
from viam.proto.app.robot import ComponentConfig
from viam.resource.base import ResourceBase
from viam.resource.types import Model, ModelFamily
from viam.utils import SensorReading, struct_to_dict
from viam.logging import getLogger

LOGGER = getLogger(__name__)

class BaseStockAlert(Sensor):
    """Base class for stock alert implementations."""
    def __init__(self, name: str):
        super().__init__(name)
        self.dependencies = {}
        self.location = ""  # Location of the stock monitoring
        self.descriptor = "Areas of Interest"  # Default descriptor
        self.areas = []  # List of specific areas of interest

    async def get_readings(self, *, extra: Optional[Mapping[str, Any]] = None, timeout: Optional[float] = None, **kwargs) -> Mapping[str, SensorReading]:
        sensor: Sensor = self.dependencies["langer_fill"]
        try:
            readings = await sensor.get_readings()
            empty_areas = [k for k, v in readings["readings"].items() if isinstance(v, (int, float)) and v == 0 and k in self.areas]
            if empty_areas:
                await self.send_alert(empty_areas)
            return {"empty_areas": empty_areas}
        except Exception as e:
            LOGGER.error(f"Error in get_readings: {e}")
            return {"empty_areas": []}

    async def send_alert(self, empty_areas: list[str]):
        raise NotImplementedError("Subclasses must implement send_alert")

    async def run_loop(self):
        while True:
            await self.get_readings()
            await asyncio.sleep(900)  # 15 minutes

class StockAlertEmail(BaseStockAlert):
    MODEL = Model(ModelFamily("hunter", "stock-alert"), "email")
    
    def __init__(self, name: str):
        super().__init__(name)
        self.recipients = []

    @classmethod
    def new(cls, config: ComponentConfig, dependencies: Mapping[str, ResourceBase]) -> "StockAlertEmail":
        alerter = cls(config.name)
        alerter.reconfigure(config, dependencies)
        return alerter

    @classmethod
    def validate_config(cls, config: ComponentConfig) -> list[str]:
        if not config.attributes.fields["location"].string_value:
            raise ValueError("location must be specified")
        attributes = struct_to_dict(config.attributes)
        recipients = attributes.get("recipients", [])
        if not recipients or not isinstance(recipients, list) or not all(isinstance(r, str) for r in recipients):
            raise ValueError("recipients must be a non-empty list of email addresses")
        areas = attributes.get("areas", [])
        if not areas or not isinstance(areas, list) or not all(isinstance(a, str) for a in areas):
            raise ValueError("areas must be a non-empty list of area identifiers")
        descriptor = attributes.get("descriptor", "Areas of Interest")
        if not isinstance(descriptor, str):
            raise ValueError("descriptor must be a string")
        return ["langer_fill", "sendgrid_email"]

    def reconfigure(self, config: ComponentConfig, dependencies: Mapping[str, ResourceBase]):
        self.location = config.attributes.fields["location"].string_value
        self.recipients = struct_to_dict(config.attributes).get("recipients", [])
        self.areas = struct_to_dict(config.attributes).get("areas", [])
        self.descriptor = struct_to_dict(config.attributes).get("descriptor", "Areas of Interest")
        self.dependencies = dependencies
        asyncio.create_task(self.run_loop())

    async def send_alert(self, empty_areas: list[str]):
        email: Generic = self.dependencies["sendgrid_email"]
        subject = f"{self.location} - Empty {self.descriptor}: {', '.join(empty_areas)}"
        await email.do_command({
            "command": "send",
            "to": self.recipients,
            "subject": subject,
            "body": ""
        }, timeout=10)
        LOGGER.info(f"Sent email for empty {self.descriptor.lower()}: {empty_areas}")

# Example future SMS model (commented out)
# class StockAlertSMS(BaseStockAlert):
#     MODEL = Model(ModelFamily("hunter", "stock-alert"), "sms")
#     
#     def __init__(self, name: str):
#         super().__init__(name)
#         self.phone_numbers = []
#     
#     @classmethod
#     def validate_config(cls, config: ComponentConfig) -> list[str]:
#         if not config.attributes.fields["location"].string_value:
#             raise ValueError("location must be specified")
#         attributes = struct_to_dict(config.attributes)
#         phones = attributes.get("phone_numbers", [])
#         if not phones or not isinstance(phones, list) or not all(isinstance(p, str) for p in phones):
#             raise ValueError("phone_numbers must be a non-empty list of phone numbers")
#         areas = attributes.get("areas", [])
#         if not areas or not isinstance(areas, list) or not all(isinstance(a, str) for a in areas):
#             raise ValueError("areas must be a non-empty list of area identifiers")
#         descriptor = attributes.get("descriptor", "Areas of Interest")
#         if not isinstance(descriptor, str):
#             raise ValueError("descriptor must be a string")
#         return ["langer_fill", "twilio_sms"]
#     
#     def reconfigure(self, config: ComponentConfig, dependencies: Mapping[str, ResourceBase]):
#         self.location = config.attributes.fields["location"].string_value
#         self.phone_numbers = struct_to_dict(config.attributes).get("phone_numbers", [])
#         self.areas = struct_to_dict(config.attributes).get("areas", [])
#         self.descriptor = struct_to_dict(config.attributes).get("descriptor", "Areas of Interest")
#         self.dependencies = dependencies
#         asyncio.create_task(self.run_loop())
#     
#     async def send_alert(self, empty_areas: list[str]):
#         sms: Generic = self.dependencies["twilio_sms"]
#         message = f"{self.location} - Empty {self.descriptor}: {', '.join(empty_areas)}"
#         await sms.do_command({"command": "send", "to": self.phone_numbers, "message": message})

async def main():
    module = Module.from_args()
    module.add_model_from_registry(Sensor.API, StockAlertEmail.MODEL, StockAlertEmail.new)
    # module.add_model_from_registry(Sensor.API, StockAlertSMS.MODEL, StockAlertSMS.new)
    await module.start()

if __name__ == "__main__":
    asyncio.run(main())