#!/usr/bin/env python3
import asyncio
import datetime
from typing import Mapping, Optional, Any, List
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
        
        # Scheduling parameters
        self.start_time = "07:00"  # Default start time (7 AM)
        self.end_time = "19:00"    # Default end time (7 PM)
        self.interval_minutes = 15  # Run every 15 minutes
        
        # Monitoring state
        self.last_check_time = None
        self.empty_areas_history = {}
        self.total_alerts_sent = 0
        self.last_alert_time = None
        self.next_scheduled_check = None
        self.is_within_operating_hours = False
        self.status = "initializing"

    async def get_readings(self, *, extra: Optional[Mapping[str, Any]] = None, timeout: Optional[float] = None, **kwargs) -> Mapping[str, SensorReading]:
        """Get current sensor readings including monitor status and empty areas."""
        current_time = datetime.datetime.now()
        
        # Update state tracking for readings
        self.is_within_operating_hours = self._is_within_operating_hours(current_time)
        self.next_scheduled_check = self._get_next_check_time(current_time)
        
        # Only attempt to get readings from the fill sensor if it's available and within operating hours
        empty_areas = []
        if self.is_within_operating_hours:
            # Improved dependency handling
            fill_sensor = None
            for name, resource in self.dependencies.items():
                # Convert name to string before using .lower()
                name_str = str(name)
                if isinstance(resource, Sensor) and "langer_fill" in name_str:
                    fill_sensor = resource
                    break
                    
            if not fill_sensor:
                LOGGER.warning("langer_fill sensor not available yet, retrying later")
                self.status = "waiting_for_dependency"
            else:
                try:
                    readings = await fill_sensor.get_readings()
                    empty_areas = [k for k, v in readings["readings"].items() 
                                if isinstance(v, (int, float)) and v == 0 and k in self.areas]
                    
                    # Record empty areas in history with timestamp
                    self.empty_areas_history[current_time.strftime("%Y-%m-%d %H:%M:%S")] = empty_areas
                    self.last_check_time = current_time
                    self.status = "active"
                    
                    # Trigger alert if needed
                    if empty_areas:
                        LOGGER.info(f"Found {len(empty_areas)} empty areas: {', '.join(empty_areas)}")
                        await self.send_alert(empty_areas)
                    else:
                        LOGGER.debug("No empty areas found during check")
                except Exception as e:
                    LOGGER.error(f"Error in get_readings: {e}")
                    self.status = f"error: {str(e)}"
            else:
                self.status = "outside_operating_hours"
                LOGGER.debug(f"Not checking - outside operating hours ({self.start_time}-{self.end_time})")
                
        # Comprehensive readings for monitoring
        return {
            "empty_areas": empty_areas,
            "status": self.status,
            "location": self.location,
            "last_check_time": str(self.last_check_time) if self.last_check_time else "never",
            "next_scheduled_check": str(self.next_scheduled_check) if self.next_scheduled_check else "unknown",
            "total_alerts_sent": self.total_alerts_sent,
            "last_alert_time": str(self.last_alert_time) if self.last_alert_time else "never",
            "within_operating_hours": self.is_within_operating_hours,
            "operating_hours": f"{self.start_time} to {self.end_time}",
            "interval_minutes": self.interval_minutes,
            "areas_monitored": self.areas
        }

    def _is_within_operating_hours(self, current_time: datetime.datetime) -> bool:
        """Determine if current time is within operating hours."""
        try:
            current_time_str = current_time.strftime("%H:%M")
            return self.start_time <= current_time_str <= self.end_time
        except Exception as e:
            LOGGER.error(f"Error checking operating hours: {e}")
            return True  # Default to active if there's an error

    def _get_next_check_time(self, current_time: datetime.datetime) -> datetime.datetime:
        """Calculate the next scheduled check time based on the interval."""
        try:
            # Parse operating hours
            start_hour, start_minute = map(int, self.start_time.split(":"))
            end_hour, end_minute = map(int, self.end_time.split(":"))
            
            # Start with current time
            next_check = current_time
            
            # Round up to the next interval
            minutes = next_check.minute
            remainder = minutes % self.interval_minutes
            if remainder > 0:
                minutes_to_add = self.interval_minutes - remainder
                next_check = next_check + datetime.timedelta(minutes=minutes_to_add)
            
            # Reset seconds and microseconds
            next_check = next_check.replace(second=0, microsecond=0)
            
            # Handle case where next check would be outside operating hours
            next_check_time_str = next_check.strftime("%H:%M")
            
            # If next check is before start time, set to start time
            if next_check_time_str < self.start_time:
                # If current day, use today's start time
                if current_time.strftime("%Y-%m-%d") == next_check.strftime("%Y-%m-%d"):
                    next_check = next_check.replace(hour=start_hour, minute=start_minute)
                # If it's for tomorrow, add a day
                else:
                    next_check = (next_check + datetime.timedelta(days=1)).replace(
                        hour=start_hour, minute=start_minute)
            
            # If next check is after end time, set to next day's start time
            elif next_check_time_str > self.end_time:
                next_check = (next_check + datetime.timedelta(days=1)).replace(
                    hour=start_hour, minute=start_minute)
                
            return next_check
        except Exception as e:
            LOGGER.error(f"Error calculating next check time: {e}")
            # Fallback to simple interval if there's an error
            return current_time + datetime.timedelta(seconds=900)

    async def send_alert(self, empty_areas: List[str]):
        """Send alert for empty areas - implemented by subclasses."""
        self.last_alert_time = datetime.datetime.now()
        self.total_alerts_sent += 1
        raise NotImplementedError("Subclasses must implement send_alert")

    async def run_checks_loop(self):
        """Run checks at specific scheduled times based on interval."""
        LOGGER.info(f"Starting scheduled checks loop for {self.name} at {self.interval_minutes} minute intervals")
        while True:
            try:
                current_time = datetime.datetime.now()
                
                # Skip checks outside operating hours
                if not self._is_within_operating_hours(current_time):
                    # Calculate time until next operating hours begin
                    start_hour, start_minute = map(int, self.start_time.split(":"))
                    tomorrow = current_time + datetime.timedelta(days=1)
                    next_start = current_time.replace(hour=start_hour, minute=start_minute)
                    
                    # If we're past today's start time, use tomorrow's
                    if current_time.strftime("%H:%M") > self.start_time:
                        next_start = tomorrow.replace(hour=start_hour, minute=start_minute)
                    
                    sleep_seconds = (next_start - current_time).total_seconds()
                    LOGGER.info(f"Outside operating hours. Sleeping until {next_start}")
                    
                    # Cap sleep time to avoid very long sleeps
                    sleep_seconds = min(sleep_seconds, 3600)  # Max 1 hour sleep
                    await asyncio.sleep(sleep_seconds)
                    continue

                # If we're within operating hours, calculate time to next interval
                next_check = self._get_next_check_time(current_time)
                sleep_seconds = (next_check - current_time).total_seconds()
                
                # Skip if past end time
                if next_check.strftime("%H:%M") > self.end_time:
                    # Sleep until next start
                    start_hour, start_minute = map(int, self.start_time.split(":"))
                    next_day = current_time + datetime.timedelta(days=1)
                    next_start = next_day.replace(hour=start_hour, minute=start_minute)
                    sleep_seconds = (next_start - current_time).total_seconds()
                    LOGGER.info(f"Past end time. Sleeping until next start time at {next_start}")
                    
                    # Cap sleep time
                    sleep_seconds = min(sleep_seconds, 3600)
                    await asyncio.sleep(sleep_seconds)
                    continue
                
                # Sleep until next check time
                LOGGER.debug(f"Sleeping for {sleep_seconds:.1f} seconds until next check at {next_check}")
                self.next_scheduled_check = next_check
                
                # Break the sleep into smaller chunks so we can respond to configuration changes
                chunk_size = 30  # 30 seconds
                remaining = sleep_seconds
                while remaining > 0:
                    await asyncio.sleep(min(chunk_size, remaining))
                    remaining -= chunk_size
                    # Check if configuration has changed (operating hours)
                    if not self._is_within_operating_hours(datetime.datetime.now()):
                        LOGGER.info("Operating hours changed during sleep, recalculating schedule")
                        break
                
                # Run check at the scheduled time
                if self._is_within_operating_hours(datetime.datetime.now()):
                    LOGGER.info(f"Running scheduled check at {datetime.datetime.now()}")
                    await self.get_readings()
            except Exception as e:
                LOGGER.error(f"Error in check loop: {e}")
                await asyncio.sleep(60)  # Sleep and retry on error

class StockAlertEmail(BaseStockAlert):
    MODEL = Model(ModelFamily("hunter", "stock-alert"), "email")
    
    def __init__(self, name: str):
        super().__init__(name)
        self.recipients = []
        self.email_history = []  # Track sent emails

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
        
        # Validate recipients
        recipients = attributes.get("recipients", [])
        if not recipients or not isinstance(recipients, list) or not all(isinstance(r, str) for r in recipients):
            raise ValueError("recipients must be a non-empty list of email addresses")
        
        # Validate areas
        areas = attributes.get("areas", [])
        if not areas or not isinstance(areas, list) or not all(isinstance(a, str) for a in areas):
            raise ValueError("areas must be a non-empty list of area identifiers")
        
        # Validate descriptor
        descriptor = attributes.get("descriptor", "Areas of Interest")
        if not isinstance(descriptor, str):
            raise ValueError("descriptor must be a string")
        
        # Validate operating hours
        start_time = attributes.get("start_time", "07:00")
        end_time = attributes.get("end_time", "19:00")
        try:
            datetime.datetime.strptime(start_time, "%H:%M")
            datetime.datetime.strptime(end_time, "%H:%M")
        except ValueError:
            raise ValueError("start_time and end_time must be in HH:MM format")
        
        # Validate interval
        interval = attributes.get("interval_minutes", 15)
        if not isinstance(interval, int) or interval < 1:
            raise ValueError("interval_minutes must be a positive integer")
        
        return ["langer_fill", "sendgrid_email"]

    def reconfigure(self, config: ComponentConfig, dependencies: Mapping[str, ResourceBase]):
        """Configure the stock alert with updated settings."""
        # Basic configuration
        self.location = config.attributes.fields["location"].string_value
        
        # Get attributes as dictionary
        attributes = struct_to_dict(config.attributes)
        
        # Configure alert settings
        self.recipients = attributes.get("recipients", [])
        self.areas = attributes.get("areas", [])
        self.descriptor = attributes.get("descriptor", "Areas of Interest")
        
        # Configure scheduling
        self.start_time = attributes.get("start_time", "07:00")
        self.end_time = attributes.get("end_time", "19:00")
        self.interval_minutes = attributes.get("interval_minutes", 15)
        
        # Store dependencies
        self.dependencies = dependencies
        LOGGER.info(f"Dependencies received: {list(self.dependencies.keys())}")
        
        # Log configuration
        LOGGER.info(f"Configured {self.name} for location '{self.location}'")
        LOGGER.info(f"Operating hours: {self.start_time} to {self.end_time}, checking every {self.interval_minutes} minutes")
        LOGGER.info(f"Monitoring areas: {', '.join(self.areas)}")
        LOGGER.info(f"Will send alerts to: {', '.join(self.recipients)}")
        
        # Start the monitoring loop in a background task
        # Cancel existing task if it exists to prevent multiple loops
        if hasattr(self, '_check_task') and self._check_task:
            self._check_task.cancel()
        
        self._check_task = asyncio.create_task(self.run_checks_loop())
        self.status = "configured"

    async def send_alert(self, empty_areas: List[str]):
        """Send email alert for empty areas using SendGrid service."""
        # Find SendGrid email service in dependencies
        email_service = None
        for name, resource in self.dependencies.items():
            # Convert name to string before using .lower()
            name_str = str(name)
            if isinstance(resource, Generic) and "sendgrid_email" in name_str:
                email_service = resource
                break
                
        if not email_service:
            LOGGER.warning("sendgrid_email service not available, skipping alert")
            return
        
        try:
            subject = f"{self.location} - Empty {self.descriptor}: {', '.join(empty_areas)}"
            
            # Log the alert
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            LOGGER.info(f"Sending email alert at {timestamp} for empty {self.descriptor.lower()}: {', '.join(empty_areas)}")
            
            # Send the email
            await email_service.do_command({
                "command": "send",
                "to": self.recipients,
                "subject": subject,
                "body": f"The following {self.descriptor.lower()} are empty and need attention: {', '.join(empty_areas)}\n\nLocation: {self.location}\nTime: {timestamp}"
            }, timeout=10)
            
            # Update state
            self.last_alert_time = datetime.datetime.now()
            self.total_alerts_sent += 1
            
            # Record in history
            self.email_history.append({
                "timestamp": timestamp,
                "recipients": self.recipients,
                "subject": subject,
                "empty_areas": empty_areas
            })
            
            LOGGER.info(f"Successfully sent email alert to {len(self.recipients)} recipients")
        except Exception as e:
            LOGGER.error(f"Failed to send email alert: {e}")
            raise

    async def do_command(self, command: dict, *, timeout: Optional[float] = None, **kwargs) -> dict:
        """Handle custom commands for the sensor."""
        cmd = command.get("command", "")
        
        if cmd == "check_now":
            # Force an immediate check
            LOGGER.info("Received command to check now")
            readings = await self.get_readings()
            return {
                "status": "completed",
                "readings": readings
            }
            
        elif cmd == "get_history":
            # Return alert history
            days = int(command.get("days", 1))
            cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
            cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
            
            # Filter history by date
            filtered_history = [
                entry for entry in self.email_history 
                if entry["timestamp"] >= cutoff_str
            ]
            
            return {
                "status": "completed",
                "history": filtered_history,
                "days": days,
                "count": len(filtered_history)
            }
            
        elif cmd == "update_schedule":
            # Update the scheduling parameters
            if "start_time" in command:
                self.start_time = command["start_time"]
            if "end_time" in command:
                self.end_time = command["end_time"]
            if "interval_minutes" in command:
                self.interval_minutes = int(command["interval_minutes"])
                
            LOGGER.info(f"Updated schedule: {self.start_time} to {self.end_time}, every {self.interval_minutes} minutes")
            
            # Recalculate next check time
            self.next_scheduled_check = self._get_next_check_time(datetime.datetime.now())
            
            return {
                "status": "updated",
                "operating_hours": f"{self.start_time} to {self.end_time}",
                "interval_minutes": self.interval_minutes,
                "next_check": str(self.next_scheduled_check)
            }
        
        else:
            return {
                "status": "error",
                "message": f"Unknown command: {cmd}"
            }

class StockAlertSMS(BaseStockAlert):
    MODEL = Model(ModelFamily("hunter", "stock-alert"), "sms")
    
    def __init__(self, name: str):
        super().__init__(name)
        self.phone_numbers = []
        self.sms_history = []
    
    @classmethod
    def new(cls, config: ComponentConfig, dependencies: Mapping[str, ResourceBase]) -> "StockAlertSMS":
        alerter = cls(config.name)
        alerter.reconfigure(config, dependencies)
        return alerter
    
    @classmethod
    def validate_config(cls, config: ComponentConfig) -> list[str]:
        if not config.attributes.fields["location"].string_value:
            raise ValueError("location must be specified")
        
        attributes = struct_to_dict(config.attributes)
        
        # Validate phone numbers
        phones = attributes.get("phone_numbers", [])
        if not phones or not isinstance(phones, list) or not all(isinstance(p, str) for p in phones):
            raise ValueError("phone_numbers must be a non-empty list of phone numbers")
        
        # Validate areas
        areas = attributes.get("areas", [])
        if not areas or not isinstance(areas, list) or not all(isinstance(a, str) for a in areas):
            raise ValueError("areas must be a non-empty list of area identifiers")
        
        # Validate descriptor
        descriptor = attributes.get("descriptor", "Areas of Interest")
        if not isinstance(descriptor, str):
            raise ValueError("descriptor must be a string")
        
        # Validate operating hours
        start_time = attributes.get("start_time", "07:00")
        end_time = attributes.get("end_time", "19:00")
        try:
            datetime.datetime.strptime(start_time, "%H:%M")
            datetime.datetime.strptime(end_time, "%H:%M")
        except ValueError:
            raise ValueError("start_time and end_time must be in HH:MM format")
        
        # Validate interval
        interval = attributes.get("interval_minutes", 15)
        if not isinstance(interval, int) or interval < 1:
            raise ValueError("interval_minutes must be a positive integer")
        
        return ["langer_fill", "twilio_sms"]
    
    def reconfigure(self, config: ComponentConfig, dependencies: Mapping[str, ResourceBase]):
        """Configure the SMS stock alert with updated settings."""
        # Basic configuration
        self.location = config.attributes.fields["location"].string_value
        
        # Get attributes as dictionary
        attributes = struct_to_dict(config.attributes)
        
        # Configure alert settings
        self.phone_numbers = attributes.get("phone_numbers", [])
        self.areas = attributes.get("areas", [])
        self.descriptor = attributes.get("descriptor", "Areas of Interest")
        
        # Configure scheduling
        self.start_time = attributes.get("start_time", "07:00")
        self.end_time = attributes.get("end_time", "19:00")
        self.interval_minutes = attributes.get("interval_minutes", 15)
        
        # Store dependencies
        self.dependencies = dependencies
        LOGGER.info(f"Dependencies received: {list(self.dependencies.keys())}")
        
        # Log configuration
        LOGGER.info(f"Configured {self.name} for location '{self.location}'")
        LOGGER.info(f"Operating hours: {self.start_time} to {self.end_time}, checking every {self.interval_minutes} minutes")
        LOGGER.info(f"Monitoring areas: {', '.join(self.areas)}")
        LOGGER.info(f"Will send SMS alerts to: {', '.join(self.phone_numbers)}")
        
        # Start the monitoring loop in a background task
        # Cancel existing task if it exists to prevent multiple loops
        if hasattr(self, '_check_task') and self._check_task:
            self._check_task.cancel()
        
        self._check_task = asyncio.create_task(self.run_checks_loop())
        self.status = "configured"
    
    async def send_alert(self, empty_areas: List[str]):
        """Send SMS alert for empty areas using Twilio service."""
        # Find Twilio SMS service in dependencies
        sms_service = None
        for name, resource in self.dependencies.items():
            # Convert name to string before using .lower()
            name_str = str(name)
            if isinstance(resource, Generic) and "twilio_sms" in name_str:
                sms_service = resource
                break
                
        if not sms_service:
            LOGGER.warning("twilio_sms service not available, skipping alert")
            return
        
        try:
            message = f"{self.location} - Empty {self.descriptor}: {', '.join(empty_areas)}"
            
            # Log the alert
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            LOGGER.info(f"Sending SMS alert at {timestamp} for empty {self.descriptor.lower()}: {', '.join(empty_areas)}")
            
            # Send the SMS
            await sms_service.do_command({
                "command": "send", 
                "to": self.phone_numbers, 
                "message": message
            }, timeout=10)
            
            # Update state
            self.last_alert_time = datetime.datetime.now()
            self.total_alerts_sent += 1
            
            # Record in history
            self.sms_history.append({
                "timestamp": timestamp,
                "recipients": self.phone_numbers,
                "message": message,
                "empty_areas": empty_areas
            })
            
            LOGGER.info(f"Successfully sent SMS alert to {len(self.phone_numbers)} recipients")
        except Exception as e:
            LOGGER.error(f"Failed to send SMS alert: {e}")
            raise

async def main():
    """Initialize and start the module."""
    try:
        LOGGER.info("Starting the stock-alert module")
        module = Module.from_args()
        
        # Register the email model
        module.add_model_from_registry(Sensor.API, StockAlertEmail.MODEL)
        LOGGER.info(f"Registered model: {StockAlertEmail.MODEL}")
        
        # Register the SMS model
        module.add_model_from_registry(Sensor.API, StockAlertSMS.MODEL)
        LOGGER.info(f"Registered model: {StockAlertSMS.MODEL}")
        
        # Start the module
        LOGGER.info("Starting module...")
        await module.start()
    except Exception as e:
        LOGGER.error(f"Failed to start module: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())