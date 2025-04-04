import asyncio
import datetime
import json
import os
import fasteners
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

class StockAlertEmail(Sensor):
    MODEL = Model(ModelFamily("hunter", "stock-alert"), "email")
    
    def __init__(self, name: str):
        super().__init__(name)
        self.dependencies = {}
        self.location = ""
        self.descriptor = "Areas of Interest"
        self.areas = []
        self.recipients = []
        
        # Simplified scheduling
        self.weekdays_only = True
        self.check_times = []
        
        # State
        self.last_check_time = None
        self.empty_areas = []
        self.total_alerts_sent = 0
        self.last_alert_time = None
        
        # State persistence and locking
        self.state_dir = os.path.join(os.path.expanduser("~"), ".stock-alert")
        self.state_file = os.path.join(self.state_dir, f"{name}.json")
        self.lock_file = os.path.join(self.state_dir, f"{name}.lock")
        os.makedirs(self.state_dir, exist_ok=True)
        
        # Background task
        self._check_task = None
        
        # Load state
        self._load_state()
    
    def _load_state(self):
        """Load persistent state from file with locking."""
        if os.path.exists(self.state_file):
            # Use a file lock to ensure safe reads
            lock = fasteners.InterProcessLock(f"{self.state_file}.lock")
            
            try:
                # Acquire the lock with a timeout
                if lock.acquire(blocking=True, timeout=5):
                    try:
                        with open(self.state_file, "r") as f:
                            state = json.load(f)
                            self.last_check_time = (
                                datetime.datetime.fromisoformat(state["last_check_time"])
                                if state.get("last_check_time")
                                else None
                            )
                            self.last_alert_time = (
                                datetime.datetime.fromisoformat(state["last_alert_time"])
                                if state.get("last_alert_time")
                                else None
                            )
                            self.total_alerts_sent = state.get("total_alerts_sent", 0)
                            self.empty_areas = state.get("empty_areas", [])
                            
                        LOGGER.info(f"Loaded state from {self.state_file}: last_check_time={self.last_check_time}, " 
                                    f"last_alert_time={self.last_alert_time}, total_alerts_sent={self.total_alerts_sent}")
                    finally:
                        lock.release()
                else:
                    LOGGER.warning(f"Could not acquire lock to load state for {self.name}")
            except Exception as e:
                LOGGER.error(f"Error loading state: {e}")
        else:
            LOGGER.info(f"No state file at {self.state_file}, starting fresh")
    
    def _save_state(self):
        """Save state to file for persistence across restarts using file locking."""
        # Use a file lock to ensure safe writes
        lock = fasteners.InterProcessLock(f"{self.state_file}.lock")
        
        try:
            # Acquire the lock with a timeout
            if lock.acquire(blocking=True, timeout=5):
                try:
                    state = {
                        "last_check_time": self.last_check_time.isoformat() if self.last_check_time else None,
                        "last_alert_time": self.last_alert_time.isoformat() if self.last_alert_time else None,
                        "total_alerts_sent": self.total_alerts_sent,
                        "empty_areas": self.empty_areas
                    }
                    
                    # First write to a temporary file
                    temp_file = f"{self.state_file}.tmp"
                    with open(temp_file, "w") as f:
                        json.dump(state, f)
                    
                    # Then atomically replace the original file
                    os.replace(temp_file, self.state_file)
                    
                    LOGGER.debug(f"Saved state to {self.state_file}")
                finally:
                    lock.release()
            else:
                LOGGER.warning(f"Could not acquire lock to save state for {self.name}")
        except Exception as e:
            LOGGER.error(f"Error saving state: {e}")
    
    @classmethod
    def new(cls, config: ComponentConfig, dependencies: Mapping[str, ResourceBase]) -> "StockAlertEmail":
        alerter = cls(config.name)
        alerter.reconfigure(config, dependencies)
        return alerter

    @classmethod
    def validate_config(cls, config: ComponentConfig) -> list[str]:
        """Validate the configuration and return required dependencies."""
        if not config.attributes.fields["location"].string_value:
            raise ValueError("location must be specified")
        
        attributes = struct_to_dict(config.attributes)
        
        # Validate recipients
        recipients = attributes.get("recipients", [])
        if not recipients or not isinstance(recipients, list):
            raise ValueError("recipients must be a non-empty list of email addresses")
        
        # Validate areas
        areas = attributes.get("areas", [])
        if not areas or not isinstance(areas, list):
            raise ValueError("areas must be a non-empty list of area identifiers")
        
        # Return required dependencies - always include both
        deps = ["remote-1:langer_fill", "email"]
        LOGGER.info(f"StockAlertEmail.validate_config returning dependencies: {deps}")
        return deps
    
    def reconfigure(self, config: ComponentConfig, dependencies: Mapping[str, ResourceBase]):
        """Configure the stock alert with updated settings."""
        # Cancel existing task if it exists
        if self._check_task and not self._check_task.done():
            self._check_task.cancel()
        
        # Basic configuration
        self.location = config.attributes.fields["location"].string_value
        attributes = struct_to_dict(config.attributes)
        
        # Configure alert settings
        self.recipients = attributes.get("recipients", [])
        self.areas = attributes.get("areas", [])
        self.descriptor = attributes.get("descriptor", "Areas of Interest")
        
        # Simplified scheduling configuration
        self.weekdays_only = attributes.get("weekdays_only", True)
        if isinstance(self.weekdays_only, str):
            self.weekdays_only = self.weekdays_only.lower() == "true"
        
        # Direct check times configuration
        # First, check if check_times is provided
        if "check_times" in attributes:
            self.check_times = attributes["check_times"]
        else:
            # Backward compatibility with morning/afternoon style config
            self.check_times = []
            
            # Add morning check times
            morning_times = attributes.get("morning_check_times", ["08:15", "08:30", "10:15", "10:30"])
            self.check_times.extend(morning_times)
            
            # Generate afternoon check times
            start_time = attributes.get("afternoon_start_time", "10:45")
            end_time = attributes.get("afternoon_end_time", "15:00")
            interval_minutes = int(attributes.get("interval_minutes", 15))
            
            # Generate times at regular intervals
            try:
                current = datetime.datetime.strptime(start_time, "%H:%M")
                end = datetime.datetime.strptime(end_time, "%H:%M")
                
                while current <= end:
                    self.check_times.append(current.strftime("%H:%M"))
                    current += datetime.timedelta(minutes=interval_minutes)
            except Exception as e:
                LOGGER.error(f"Error generating check times: {e}")
        
        # Sort and deduplicate
        self.check_times = sorted(list(set(self.check_times)))
        
        # Store dependencies
        self.dependencies = dependencies
        
        # Start the monitoring loop
        self._check_task = asyncio.create_task(self.run_checks_loop())
        
        LOGGER.info(f"Configured {self.name} for location '{self.location}'")
        LOGGER.info(f"Weekdays only: {self.weekdays_only}")
        LOGGER.info(f"Check times: {', '.join(self.check_times)}")
        LOGGER.info(f"Monitoring areas: {', '.join(self.areas)}")
        LOGGER.info(f"Will send alerts to: {', '.join(self.recipients)}")
    
    async def get_readings(self, *, extra: Optional[Mapping[str, Any]] = None, timeout: Optional[float] = None, **kwargs) -> Mapping[str, SensorReading]:
        """Get current sensor readings."""
        current_time = datetime.datetime.now()
        
        # Calculate next check time
        next_check = self._get_next_check_time(current_time)
        
        return {
            "empty_areas": self.empty_areas,
            "location": self.location,
            "last_check_time": str(self.last_check_time) if self.last_check_time else "never",
            "next_scheduled_check": str(next_check) if next_check else "none scheduled",
            "total_alerts_sent": self.total_alerts_sent,
            "last_alert_time": str(self.last_alert_time) if self.last_alert_time else "never",
            "weekdays_only": self.weekdays_only,
            "check_times": self.check_times,
            "areas_monitored": self.areas
        }
    
    def _is_weekday(self, date: datetime.date) -> bool:
        """Check if the given date is a weekday (0=Monday, 6=Sunday)."""
        return date.weekday() < 5  # 0-4 are weekdays (Monday-Friday)
    
    def _get_next_check_time(self, current_time: datetime.datetime) -> Optional[datetime.datetime]:
        """Find the next scheduled check time from now."""
        today = current_time.date()
        current_time_str = current_time.strftime("%H:%M")
        
        # Skip weekends if weekdays_only is True
        if self.weekdays_only and not self._is_weekday(today):
            # Find next weekday
            days_ahead = 1
            while not self._is_weekday(today + datetime.timedelta(days=days_ahead)):
                days_ahead += 1
            next_day = today + datetime.timedelta(days=days_ahead)
            
            # Return first check time on next weekday
            if self.check_times:
                time_str = self.check_times[0]
                hour, minute = map(int, time_str.split(":"))
                return datetime.datetime.combine(next_day, datetime.time(hour, minute))
            return None
        
        # Find next check time today
        for time_str in self.check_times:
            if time_str > current_time_str:
                hour, minute = map(int, time_str.split(":"))
                return datetime.datetime.combine(today, datetime.time(hour, minute))
        
        # If no more today, get tomorrow's first time
        tomorrow = today + datetime.timedelta(days=1)
        
        # Skip to next weekday if needed
        if self.weekdays_only and not self._is_weekday(tomorrow):
            days_ahead = 1
            while not self._is_weekday(tomorrow + datetime.timedelta(days=days_ahead - 1)):
                days_ahead += 1
            tomorrow = today + datetime.timedelta(days=days_ahead)
        
        if self.check_times:
            time_str = self.check_times[0]
            hour, minute = map(int, time_str.split(":"))
            return datetime.datetime.combine(tomorrow, datetime.time(hour, minute))
        
        return None
    
    def _is_check_time(self, current_time: datetime.datetime) -> bool:
        """Check if the current time is a scheduled check time."""
        if self.weekdays_only and not self._is_weekday(current_time.date()):
            return False
        
        current_time_str = current_time.strftime("%H:%M")
        return current_time_str in self.check_times
    
    async def send_alert(self, empty_areas: List[str]):
        """Send email alert for empty areas."""
        # Find email service
        email_service = None
        for name, resource in self.dependencies.items():
            if isinstance(resource, Generic) and str(name).endswith("email"):
                email_service = resource
                break
        
        # Update state
        self.last_alert_time = datetime.datetime.now()
        self.total_alerts_sent += 1
        self._save_state()
        
        # If no email service, just log
        if not email_service:
            LOGGER.warning(f"No email service available for {self.name}")
            return
        
        # Send the email
        try:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            subject = f"{self.location} - Empty {self.descriptor}: {', '.join(empty_areas)}"
            
            await email_service.do_command({
                "command": "send",
                "to": self.recipients,
                "subject": subject,
                "body": f"""The following {self.descriptor.lower()} are empty and need attention: {', '.join(empty_areas)}

Location: {self.location}
Time: {timestamp}
"""
            })
            
            LOGGER.info(f"Sent email alert to {len(self.recipients)} recipients")
        except Exception as e:
            LOGGER.error(f"Failed to send email alert: {e}")
    
    async def perform_check(self):
        """Check for empty areas and send alerts if needed."""
        # Find the fill sensor dependency
        fill_sensor = None
        for name, resource in self.dependencies.items():
            if "langer_fill" in str(name):
                fill_sensor = resource
                break
        
        if not fill_sensor:
            LOGGER.warning(f"langer_fill sensor not available for {self.name}")
            return
        
        try:
            # Get readings from fill sensor
            readings = await fill_sensor.get_readings()
            
            # Find empty areas
            empty_areas = []
            for area in self.areas:
                level = None
                
                # Try direct access first
                if area in readings:
                    level = readings[area]
                # Try nested structure
                elif isinstance(readings, dict) and "readings" in readings and area in readings["readings"]:
                    level = readings["readings"][area]
                
                # Check if area is empty
                if isinstance(level, (int, float)) and level == 0:
                    empty_areas.append(area)
            
            # Update state
            self.empty_areas = empty_areas
            self.last_check_time = datetime.datetime.now()
            self._save_state()
            
            # Send alert if needed
            if empty_areas:
                LOGGER.info(f"Found {len(empty_areas)} empty areas: {', '.join(empty_areas)}")
                await self.send_alert(empty_areas)
            else:
                LOGGER.info("No empty areas found")
                
        except Exception as e:
            LOGGER.error(f"Error checking stock levels: {e}")
    
    async def run_checks_loop(self):
        """Run the monitoring loop with process locking."""
        # Use fasteners to ensure only one instance runs the scheduled loop
        lock = fasteners.InterProcessLock(self.lock_file)
        if not lock.acquire(blocking=False):
            LOGGER.info(f"Another instance of {self.name} is already running the check loop (PID {os.getpid()})")
            return
        
        LOGGER.info(f"Starting scheduled checks loop for {self.name} (PID: {os.getpid()})")
        
        try:
            while True:
                # Get current time
                current_time = datetime.datetime.now()
                
                # Run check if it's time
                if self._is_check_time(current_time):
                    await self.perform_check()
                
                # Calculate sleep time to next check
                next_check = self._get_next_check_time(current_time)
                
                if next_check:
                    # Sleep until next check time or at most 60 seconds
                    sleep_seconds = min(60, (next_check - current_time).total_seconds())
                    if sleep_seconds <= 0:
                        sleep_seconds = 60  # Default to 1 minute if calculation is negative
                    
                    LOGGER.info(f"Next check at {next_check.strftime('%H:%M')}, sleeping for {sleep_seconds:.1f} seconds")
                    
                    # Sleep in smaller chunks to be responsive to cancellation
                    remaining = sleep_seconds
                    while remaining > 0:
                        chunk = min(remaining, 10)  # 10 second chunks
                        await asyncio.sleep(chunk)
                        remaining -= chunk
                        
                        # Check if task is being cancelled
                        if asyncio.current_task().cancelled():
                            raise asyncio.CancelledError()
                else:
                    # No scheduled checks, sleep for 5 minutes
                    LOGGER.info(f"No scheduled checks for {self.name}, sleeping for 5 minutes")
                    await asyncio.sleep(300)
                
        except asyncio.CancelledError:
            LOGGER.info(f"Check loop cancelled for {self.name}")
            raise
        except Exception as e:
            LOGGER.error(f"Error in check loop: {e}")
            # Sleep for 1 minute on error before trying again
            await asyncio.sleep(60)
        finally:
            # Make sure we release the lock
            try:
                lock.release()
                LOGGER.info(f"Released lock for {self.name}, check loop exiting (PID: {os.getpid()})")
            except Exception as e:
                LOGGER.error(f"Error releasing lock: {e}")
    
    async def do_command(self, command: dict, *, timeout: Optional[float] = None, **kwargs) -> dict:
        """Handle custom commands."""
        cmd = command.get("command", "")
        
        if cmd == "check_now":
            # Force an immediate check
            await self.perform_check()
            return {
                "status": "completed",
                "empty_areas": self.empty_areas
            }
        
        elif cmd == "get_schedule":
            # Return check schedule
            next_check = self._get_next_check_time(datetime.datetime.now())
            return {
                "status": "completed", 
                "weekdays_only": self.weekdays_only,
                "check_times": self.check_times,
                "next_scheduled_check": str(next_check) if next_check else "none scheduled"
            }
        
        else:
            return {
                "status": "error",
                "message": f"Unknown command: {cmd}"
            }