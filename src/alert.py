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
        self.weekdays_only = True  # Default to weekdays only
        
        # Fixed scheduled check times in "HH:MM" format for morning
        self.morning_check_times = ["08:15", "08:30", "10:15", "10:30"]
        
        # Afternoon scheduling
        self.afternoon_start_time = "10:45"
        self.afternoon_end_time = "15:00"
        self.interval_minutes = 15
        
        # Generated list of all check times for today
        self.today_check_times = []
        
        # Monitoring state
        self.last_check_time = None
        self.empty_areas_history = {}
        self.total_alerts_sent = 0
        self.last_alert_time = None
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
        today = current_time.date()
        
        # Update scheduling status
        is_weekday = self._is_weekday(today)
        is_within_hours = False
        
        if is_weekday or not self.weekdays_only:
            current_time_str = current_time.strftime("%H:%M")
            is_within_hours = any(time_str == current_time_str for time_str in self.today_check_times)
        
        # Get next scheduled check
        next_check = self._get_next_check_time(current_time)
        
        # Comprehensive readings
        return {
            "empty_areas": list(self.empty_areas_history.get(today.strftime("%Y-%m-%d"), [])),
            "status": self.status,
            "location": self.location,
            "last_check_time": str(self.last_check_time) if self.last_check_time else "never",
            "next_scheduled_check": str(next_check) if next_check else "none scheduled",
            "total_alerts_sent": self.total_alerts_sent,
            "last_alert_time": str(self.last_alert_time) if self.last_alert_time else "never",
            "weekdays_only": self.weekdays_only,
            "is_weekday": is_weekday,
            "today_check_times": self.today_check_times,
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

    def _get_next_check_time(self, current_time: datetime.datetime) -> Optional[datetime.datetime]:
        """Find the next scheduled check time from now."""
        today = current_time.date()
        tomorrow = today + datetime.timedelta(days=1)
        
        # If it's a weekend and we only check on weekdays, find the next weekday
        if self.weekdays_only and not self._is_weekday(today):
            days_to_add = 1
            next_day = today + datetime.timedelta(days=days_to_add)
            while not self._is_weekday(next_day):
                days_to_add += 1
                next_day = today + datetime.timedelta(days=days_to_add)
            
            # Set to the first check time on the next weekday
            self._generate_todays_check_times()  # Regenerate for the correct day
            if not self.today_check_times:
                return None
                
            next_time_str = self.today_check_times[0]
            hour, minute = map(int, next_time_str.split(":"))
            return datetime.datetime.combine(next_day, datetime.time(hour, minute))
        
        # Try to find next check time today
        current_time_str = current_time.strftime("%H:%M")
        
        # Regenerate today's check times if needed (e.g., after midnight)
        if not self.today_check_times:
            self._generate_todays_check_times()
        
        # Find the next check time today
        for time_str in self.today_check_times:
            if time_str > current_time_str:
                hour, minute = map(int, time_str.split(":"))
                return datetime.datetime.combine(today, datetime.time(hour, minute))
        
        # If we're past all check times for today, look at tomorrow
        tomorrow = today + datetime.timedelta(days=1)
        
        # If tomorrow is a weekend and we only check weekdays, find the next weekday
        if self.weekdays_only:
            days_to_add = 1
            next_day = today + datetime.timedelta(days=days_to_add)
            while not self._is_weekday(next_day):
                days_to_add += 1
                next_day = today + datetime.timedelta(days=days_to_add)
            tomorrow = next_day
        
        # Generate check times for the next day
        next_day_check_times = []
        
        # We need to temporarily change the today_check_times list
        saved_check_times = self.today_check_times
        self._generate_todays_check_times()
        next_day_check_times = self.today_check_times
        self.today_check_times = saved_check_times
        
        if not next_day_check_times:
            return None
            
        # Get the first check time for tomorrow
        time_str = next_day_check_times[0]
        hour, minute = map(int, time_str.split(":"))
        return datetime.datetime.combine(tomorrow, datetime.time(hour, minute))
    
    def _is_scheduled_check_time(self, current_time: datetime.datetime) -> bool:
        """Check if the current time is a scheduled check time."""
        today = current_time.date()
        
        # Skip weekends if weekdays_only is True
        if self.weekdays_only and not self._is_weekday(today):
            return False
        
        # Check if the current time matches any scheduled check time
        current_time_str = current_time.strftime("%H:%M")
        
        # Regenerate check times if needed
        if not self.today_check_times:
            self._generate_todays_check_times()
        
        return current_time_str in self.today_check_times

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
        """Run checks at specific scheduled times with process locking."""
        # Use fasteners to ensure only one instance runs the scheduled loop
        lock = fasteners.InterProcessLock(self.lock_file)
        if not lock.acquire(blocking=False):
            LOGGER.info(f"Another instance of {self.name} is already running the check loop (PID {os.getpid()})")
            return
            
        task_id = id(asyncio.current_task())
        LOGGER.info(f"Starting scheduled checks loop for {self.name} (task id: {task_id}, PID: {os.getpid()})")
        
        try:
            # Ensure we have today's check times
            self._generate_todays_check_times()
            
            while True:
                # Get current time and next check time
                current_time = datetime.datetime.now()
                next_check = self._get_next_check_time(current_time)
                
                if next_check is None:
                    # No check times configured, sleep for 1 hour and retry
                    LOGGER.warning(f"No check times configured for {self.name}, sleeping for 1 hour")
                    await asyncio.sleep(3600)
                    self._generate_todays_check_times()  # Try to regenerate
                    continue
                
                # Calculate sleep time
                sleep_seconds = (next_check - current_time).total_seconds()
                
                if sleep_seconds <= 0:
                    # We're already past the check time, so run now
                    LOGGER.info(f"Already past scheduled check time {next_check}, running now")
                    await self.perform_check()
                    
                    # Sleep 60 seconds to avoid rapid rechecking
                    await asyncio.sleep(60)
                    continue
                
                # Generate a short message with next check info
                next_day = "today"
                if next_check.date() > current_time.date():
                    next_day = next_check.strftime("%Y-%m-%d")
                    
                LOGGER.info(f"Next check scheduled for {next_check.strftime('%H:%M')} {next_day} (sleeping {sleep_seconds:.1f} seconds)")
                
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
                        
                    # Occasionally check if we need to regenerate today's check times
                    # (e.g., if we've crossed midnight)
                    if remaining % 300 < 30:  # Every ~5 minutes
                        now = datetime.datetime.now()
                        if now.date() > current_time.date():
                            LOGGER.info("New day detected, regenerating check times")
                            self._generate_todays_check_times()
                            # Recalculate next check time
                            break
                
                # Regenerate today's check times if we've crossed midnight
                now = datetime.datetime.now()
                if now.date() > current_time.date():
                    LOGGER.info("New day detected, regenerating check times")
                    self._generate_todays_check_times()
                    continue
                
                # Run check if it's time
                if self._is_scheduled_check_time(datetime.datetime.now()):
                    LOGGER.info(f"Running scheduled check for {self.name} at {datetime.datetime.now()}")
                    await self.perform_check()
                else:
                    LOGGER.info(f"Current time is not a scheduled check time, skipping")
                
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
        
        # Handle interval_minutes as either int or string
        interval_minutes = attributes.get("interval_minutes", 15)
        if isinstance(interval_minutes, str):
            try:
                int(interval_minutes)  # Just validate it can be converted
            except ValueError:
                raise ValueError("interval_minutes must be a positive integer")
        elif not isinstance(interval_minutes, int) or interval_minutes < 1:
            raise ValueError("interval_minutes must be a positive integer")
        
        # Validate weekdays_only (optional boolean)
        weekdays_only = attributes.get("weekdays_only", True)
        if not isinstance(weekdays_only, bool):
            if isinstance(weekdays_only, str):
                if weekdays_only.lower() not in ["true", "false"]:
                    raise ValueError("weekdays_only must be a boolean (true/false)")
            else:
                raise ValueError("weekdays_only must be a boolean")
        
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
        self.weekdays_only = attributes.get("weekdays_only", True)
        if isinstance(self.weekdays_only, str):
            self.weekdays_only = self.weekdays_only.lower() == "true"
            
        # Override morning check times if provided
        if "morning_check_times" in attributes:
            self.morning_check_times = attributes["morning_check_times"]
        
        # Configure afternoon schedule
        afternoon_start = attributes.get("afternoon_start_time", "10:45")
        afternoon_end = attributes.get("afternoon_end_time", "15:00")
        self.interval_minutes = int(attributes.get("interval_minutes", 15))
        
        self.afternoon_start_time = afternoon_start
        self.afternoon_end_time = afternoon_end
        
        # Generate today's check times
        self._generate_todays_check_times()
        
        # Store dependencies
        self.dependencies = dependencies
        LOGGER.info(f"Dependencies received for {self.name}: {list(self.dependencies.keys())}")
        
        # Log configuration
        LOGGER.info(f"Configured {self.name} for location '{self.location}'")
        LOGGER.info(f"Weekdays only: {self.weekdays_only}")
        LOGGER.info(f"Morning check times: {', '.join(self.morning_check_times)}")
        LOGGER.info(f"Afternoon schedule: {self.afternoon_start_time} to {self.afternoon_end_time} every {self.interval_minutes} minutes")
        LOGGER.info(f"Today's check times: {', '.join(self.today_check_times)}")
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
    
    def _is_weekday(self, date: datetime.date) -> bool:
        """Check if the given date is a weekday (0=Monday, 6=Sunday)."""
        return date.weekday() < 5  # 0-4 are weekdays (Monday-Friday)
    
    def _generate_todays_check_times(self):
        """Generate a list of all check times for today."""
        self.today_check_times = []
        today = datetime.datetime.now().date()
        
        # Skip weekend days if weekdays_only is True
        if self.weekdays_only and not self._is_weekday(today):
            LOGGER.info(f"Today ({today}) is a weekend, no checks scheduled")
            return

        # Add morning check times
        self.today_check_times.extend(self.morning_check_times)
        
        # Generate afternoon check times at regular intervals
        try:
            start_time = datetime.datetime.strptime(self.afternoon_start_time, "%H:%M")
            end_time = datetime.datetime.strptime(self.afternoon_end_time, "%H:%M")
            
            current_time = start_time
            while current_time <= end_time:
                time_str = current_time.strftime("%H:%M")
                self.today_check_times.append(time_str)
                current_time += datetime.timedelta(minutes=self.interval_minutes)
        except Exception as e:
            LOGGER.error(f"Error generating afternoon check times: {e}")
        
        # Sort and deduplicate all times
        self.today_check_times = sorted(list(set(self.today_check_times)))
        LOGGER.info(f"Generated {len(self.today_check_times)} check times for today")

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
            
        elif cmd == "get_schedule":
            # Return today's check schedule
            current_time = datetime.datetime.now()
            today = current_time.date()
            
            # Regenerate today's schedule if needed
            if not self.today_check_times:
                self._generate_todays_check_times()
                
            # Calculate next check time
            next_check = self._get_next_check_time(current_time)
            next_check_str = str(next_check) if next_check else "None"
            
            return {
                "status": "completed",
                "is_weekday": self._is_weekday(today),
                "weekdays_only": self.weekdays_only,
                "morning_check_times": self.morning_check_times,
                "afternoon_schedule": f"{self.afternoon_start_time} to {self.afternoon_end_time} every {self.interval_minutes} minutes",
                "today_check_times": self.today_check_times,
                "next_scheduled_check": next_check_str,
                "current_time": current_time.strftime("%H:%M")
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
                    
                    # Try to interpret readings for monitored areas
                    if isinstance(readings, dict) and "readings" in readings:
                        results["monitored_areas"] = {
                            area: readings["readings"].get(area, "not found")
                            for area in self.areas
                        }
                        
                        # Count empty areas
                        empty_areas = [k for k, v in readings["readings"].items() 
                                    if isinstance(v, (int, float)) and v == 0 and k in self.areas]
                        results["empty_areas"] = empty_areas
                        results["empty_count"] = len(empty_areas)
                    
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