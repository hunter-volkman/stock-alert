import asyncio
import datetime
import os
import json
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
        
        # Persistence and locking
        self.base_dir = "/home/hunter.volkman/stock-alert"
        self.state_file = os.path.join(self.base_dir, f"state_{name}.json")
        self.lock_file = os.path.join(self.base_dir, f"lockfile_{name}")
        self._check_task = None
        
        # Setup directory structure
        os.makedirs(self.base_dir, exist_ok=True)
        self._load_state()

    def _load_state(self):
        """Load persistent state from file."""
        if os.path.exists(self.state_file):
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
                    self.empty_areas_history = state.get("empty_areas_history", {})
                LOGGER.info(f"Loaded state from {self.state_file}: last_check_time={self.last_check_time}, last_alert_time={self.last_alert_time}, total_alerts_sent={self.total_alerts_sent}")
            except Exception as e:
                LOGGER.error(f"Error loading state: {e}")
        else:
            LOGGER.info(f"No state file at {self.state_file}, starting fresh")

    def _save_state(self):
        """Save state to file for persistence across restarts."""
        try:
            state = {
                "last_check_time": self.last_check_time.isoformat() if self.last_check_time else None,
                "last_alert_time": self.last_alert_time.isoformat() if self.last_alert_time else None,
                "total_alerts_sent": self.total_alerts_sent,
                "empty_areas_history": self.empty_areas_history
            }
            with open(self.state_file, "w") as f:
                json.dump(state, f)
            LOGGER.info(f"Saved state to {self.state_file}")
        except Exception as e:
            LOGGER.error(f"Error saving state: {e}")

    async def get_readings(self, *, extra: Optional[Mapping[str, Any]] = None, timeout: Optional[float] = None, **kwargs) -> Mapping[str, SensorReading]:
        """Get current sensor readings including monitor status and empty areas."""
        current_time = datetime.datetime.now()
        
        # Update state tracking for readings
        self.is_within_operating_hours = self._is_within_operating_hours(current_time)
        self.next_scheduled_check = self._get_next_check_time(current_time)
        
        # Only return cached data in get_readings - don't do actual checks here
        # This prevents multiple instances of check logic when UI refreshes
        
        # Comprehensive readings for monitoring
        return {
            "empty_areas": list(self.empty_areas_history.get(current_time.strftime("%Y-%m-%d"), [])),
            "status": self.status,
            "location": self.location,
            "last_check_time": str(self.last_check_time) if self.last_check_time else "never",
            "next_scheduled_check": str(self.next_scheduled_check) if self.next_scheduled_check else "unknown",
            "total_alerts_sent": self.total_alerts_sent,
            "last_alert_time": str(self.last_alert_time) if self.last_alert_time else "never",
            "within_operating_hours": self.is_within_operating_hours,
            "operating_hours": f"{self.start_time} to {self.end_time}",
            "interval_minutes": self.interval_minutes,
            "areas_monitored": self.areas,
            "pid": os.getpid()
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
            
            # If outside operating hours, return start time of next operating period
            if not self._is_within_operating_hours(current_time):
                today = current_time.date()
                next_start = current_time.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
                
                # If we're past today's start time, use tomorrow's
                if current_time >= next_start:
                    next_start = next_start + datetime.timedelta(days=1)
                return next_start
            
            # We're inside operating hours, find next interval
            # Round time to nearest interval
            minutes_since_midnight = current_time.hour * 60 + current_time.minute
            intervals_today = minutes_since_midnight // self.interval_minutes
            
            # Get next interval
            next_interval = intervals_today + 1
            next_minutes = next_interval * self.interval_minutes
            
            # Create datetime for next interval
            next_hour = next_minutes // 60
            next_minute = next_minutes % 60
            
            next_time = current_time.replace(hour=next_hour, minute=next_minute, second=0, microsecond=0)
            
            # If next interval is after end time, return start time of next day
            end_time = current_time.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
            if next_time > end_time:
                next_time = current_time.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
                next_time += datetime.timedelta(days=1)
                
            return next_time
        except Exception as e:
            LOGGER.error(f"Error calculating next check time: {e}")
            # Fallback to simple interval if there's an error
            return current_time + datetime.timedelta(minutes=self.interval_minutes)

    async def send_alert(self, empty_areas: List[str]):
        """Send alert for empty areas - implemented by subclasses."""
        self.last_alert_time = datetime.datetime.now()
        self.total_alerts_sent += 1
        self._save_state()
        raise NotImplementedError("Subclasses must implement send_alert")

    async def perform_check(self):
        """Perform actual check for empty areas."""
        current_time = datetime.datetime.now()
        empty_areas = []
        
        # Find the langer_fill sensor in dependencies
        fill_sensor = None
        for name, resource in self.dependencies.items():
            name_str = str(name)
            if isinstance(resource, Sensor) and "langer_fill" in name_str:
                fill_sensor = resource
                break
        
        if not fill_sensor:
            LOGGER.warning(f"langer_fill sensor not available yet for {self.name}")
            self.status = "waiting_for_dependency"
            return empty_areas
        
        try:
            LOGGER.info(f"Checking stock levels for {self.name}")
            readings = await fill_sensor.get_readings()
            
            # Debug the readings format
            LOGGER.debug(f"Received readings from langer_fill: {readings}")
            
            # More robust handling of readings
            if isinstance(readings, dict) and "readings" in readings:
                # Original expected format
                empty_areas = [k for k, v in readings["readings"].items() 
                            if isinstance(v, (int, float)) and v == 0 and k in self.areas]
            elif isinstance(readings, dict):
                # Alternative format - try to find readings at the top level
                empty_areas = [k for k, v in readings.items() 
                            if isinstance(v, (int, float)) and v == 0 and k in self.areas]
            else:
                LOGGER.error(f"Unexpected readings format: {type(readings)}")
                empty_areas = []
            
            # Record empty areas in history with timestamp
            today = current_time.strftime("%Y-%m-%d")
            self.empty_areas_history[today] = empty_areas
            self.last_check_time = current_time
            self.status = "active"
            
            # Trigger alert if needed
            if empty_areas:
                LOGGER.info(f"Found {len(empty_areas)} empty areas: {', '.join(empty_areas)}")
                await self.send_alert(empty_areas)
            else:
                LOGGER.debug("No empty areas found during check")
            
            # Save state after successful check
            self._save_state()
            return empty_areas
            
        except Exception as e:
            LOGGER.error(f"Error in perform_check: {e}")
            self.status = f"error: {str(e)}"
            return []

    async def run_checks_loop(self):
        """Run checks at specific scheduled times based on interval with process locking."""
        # Use fasteners to ensure only one instance runs the scheduled loop
        lock = fasteners.InterProcessLock(self.lock_file)
        if not lock.acquire(blocking=False):
            LOGGER.info(f"Another instance of {self.name} is already running the check loop (PID {os.getpid()})")
            return
            
        task_id = id(asyncio.current_task())
        LOGGER.info(f"Starting scheduled checks loop for {self.name} at {self.interval_minutes} minute intervals (task id: {task_id}, PID: {os.getpid()})")
        
        try:
            while True:
                # Get current time and next check time
                current_time = datetime.datetime.now()
                next_check = self._get_next_check_time(current_time)
                
                # Calculate sleep time
                sleep_seconds = (next_check - current_time).total_seconds()
                
                if sleep_seconds > 0:
                    # Add a small random jitter to avoid all instances checking at exactly the same time
                    sleep_seconds += (hash(self.name) % 20)  # 0-19 seconds of jitter
                    
                    LOGGER.info(f"Next check scheduled for {next_check} (sleeping {sleep_seconds:.1f} seconds)")
                    
                    # Sleep in smaller chunks to be more responsive to cancellation
                    remaining = sleep_seconds
                    while remaining > 0:
                        chunk = min(remaining, 30)  # 30 second chunks
                        await asyncio.sleep(chunk)
                        remaining -= chunk
                        
                        # Check if task is being cancelled
                        if asyncio.current_task().cancelled():
                            LOGGER.info(f"Check loop for {self.name} was cancelled during sleep")
                            raise asyncio.CancelledError()
                
                # Check if we're within operating hours now (might have changed during sleep)
                current_time = datetime.datetime.now()
                if self._is_within_operating_hours(current_time):
                    LOGGER.info(f"Running scheduled check for {self.name} at {current_time}")
                    await self.perform_check()
                else:
                    LOGGER.info(f"Skipping check at {current_time} - outside operating hours")
                
                # Small gap between checks to prevent rapid consecutive executions
                await asyncio.sleep(2)
                
        except asyncio.CancelledError:
            LOGGER.info(f"Check loop for {self.name} (task id: {task_id}) was cancelled")
            raise
        except Exception as e:
            LOGGER.error(f"Error in check loop for {self.name}: {e}")
            await asyncio.sleep(60)  # Sleep and then try to restart
        finally:
            # Make sure we release the lock
            lock.release()
            LOGGER.info(f"Released lock for {self.name}, check loop exiting (PID: {os.getpid()})")

class StockAlertEmail(BaseStockAlert):
    MODEL = Model(ModelFamily("hunter", "stock-alert"), "email")
    
    def __init__(self, name: str):
        super().__init__(name)
        self.recipients = []
        self.email_history = []  # Track sent emails
        
    @classmethod
    def new(cls, config: ComponentConfig, dependencies: Mapping[str, ResourceBase]) -> "StockAlertEmail":
        alerter = cls(config.name)
        LOGGER.info(f"Created new StockAlertEmail instance for {config.name} with PID {os.getpid()}")
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
        LOGGER.info(f"Dependencies received for {self.name}: {list(self.dependencies.keys())}")
        
        # Log configuration
        LOGGER.info(f"Configured {self.name} for location '{self.location}'")
        LOGGER.info(f"Operating hours: {self.start_time} to {self.end_time}, checking every {self.interval_minutes} minutes")
        LOGGER.info(f"Monitoring areas: {', '.join(self.areas)}")
        LOGGER.info(f"Will send alerts to: {', '.join(self.recipients)}")
        
        # Properly cancel existing task if it exists to prevent multiple loops
        if hasattr(self, '_check_task') and self._check_task:
            if not self._check_task.done() and not self._check_task.cancelled():
                LOGGER.info(f"Cancelling existing check task for {self.name}: {self._check_task}")
                self._check_task.cancel()
                try:
                    # Give the task a moment to cancel
                    asyncio.get_event_loop().run_until_complete(
                        asyncio.wait_for(asyncio.shield(self._check_task), 0.5)
                    )
                except (asyncio.CancelledError, asyncio.TimeoutError, RuntimeError):
                    pass
        
        # Start the monitoring loop in a background task
        self._check_task = asyncio.create_task(self.run_checks_loop())
        self._check_task.add_done_callback(
            lambda task: LOGGER.info(f"Check task for {self.name} completed: {task}")
        )
        self.status = "configured"

    async def send_alert(self, empty_areas: List[str]):
        """Send email alert for empty areas using SendGrid service."""
        # Find SendGrid email service in dependencies
        email_service = None
        for name, resource in self.dependencies.items():
            # Convert name to string before using string operations
            name_str = str(name)
            if isinstance(resource, Generic) and "sendgrid_email" in name_str:
                email_service = resource
                break
                
        if not email_service:
            LOGGER.warning(f"sendgrid_email service not available for {self.name}, skipping alert")
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
                "body": f"""The following {self.descriptor.lower()} are empty and need attention: {', '.join(empty_areas)}

Location: {self.location}
Time: {timestamp}
"""
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
            
            # Save state after successful alert
            self._save_state()
            
            LOGGER.info(f"Successfully sent email alert to {len(self.recipients)} recipients")
        except Exception as e:
            LOGGER.error(f"Failed to send email alert: {e}")

    async def do_command(self, command: dict, *, timeout: Optional[float] = None, **kwargs) -> dict:
        """Handle custom commands for the sensor."""
        cmd = command.get("command", "")
        
        if cmd == "check_now":
            # Force an immediate check
            LOGGER.info(f"Received command to check now for {self.name}")
            empty_areas = await self.perform_check()
            return {
                "status": "completed",
                "empty_areas": empty_areas
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
                
            LOGGER.info(f"Updated schedule for {self.name}: {self.start_time} to {self.end_time}, every {self.interval_minutes} minutes")
            
            # Recalculate next check time
            self.next_scheduled_check = self._get_next_check_time(datetime.datetime.now())
            
            return {
                "status": "updated",
                "operating_hours": f"{self.start_time} to {self.end_time}",
                "interval_minutes": self.interval_minutes,
                "next_check": str(self.next_scheduled_check)
            }
        
        elif cmd == "debug_sensor":
            # Debug the langer_fill sensor
            results = {"status": "debug"}
            try:
                # Find fill sensor
                fill_sensor = None
                for name, resource in self.dependencies.items():
                    name_str = str(name)
                    if isinstance(resource, Sensor) and "langer_fill" in name_str:
                        fill_sensor = resource
                        results["found_sensor"] = True
                        break
                
                if fill_sensor:
                    # Get readings directly
                    readings = await fill_sensor.get_readings()
                    results["raw_readings"] = readings
                else:
                    results["found_sensor"] = False
            except Exception as e:
                results["error"] = str(e)
            
            return results
        
        else:
            return {
                "status": "error",
                "message": f"Unknown command: {cmd}"
            }