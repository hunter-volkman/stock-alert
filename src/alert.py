import asyncio
import datetime
import json
import os
import base64
import fasteners
from typing import Mapping, Optional, Any, List, Dict
from viam.module.module import Module
from viam.components.sensor import Sensor
from viam.components.camera import Camera
from viam.services.generic import Generic
from viam.proto.app.robot import ComponentConfig
from viam.resource.base import ResourceBase
from viam.resource.types import Model, ModelFamily
from viam.utils import SensorReading, struct_to_dict
from viam.logging import getLogger
from viam.media.video import ViamImage

LOGGER = getLogger(__name__)

class StockAlertEmail(Sensor):
    MODEL = Model(ModelFamily("hunter", "stock-alert"), "email")
    _instances = {}  # Class-level dictionary to track instances by name
    
    @classmethod
    def new(cls, config: ComponentConfig, dependencies: Mapping[str, ResourceBase]) -> "StockAlertEmail":
        """Factory method that ensures only one instance per name exists."""
        name = config.name
        
        # Check if we already have an instance with this name
        if name in cls._instances:
            LOGGER.debug(f"Reusing existing instance of {cls.__name__} with name '{name}'")
            instance = cls._instances[name]
            # Just update its configuration
            instance.reconfigure(config, dependencies)
            return instance
            
        # Create new instance if it doesn't exist
        LOGGER.debug(f"Creating new instance of {cls.__name__} with name '{name}'")
        instance = cls(name)
        cls._instances[name] = instance
        instance.reconfigure(config, dependencies)
        return instance
    
    def __init__(self, name: str):
        super().__init__(name)
        self.dependencies = {}
        self.location = ""
        self.descriptor = "Areas of Interest"
        self.areas = []
        self.recipients = []
        
        # Camera configuration
        self.camera_name = ""
        self.include_image = False
        self.image_width = 640
        self.image_height = 480
        
        # Simplified scheduling
        self.weekdays_only = True
        self.check_times = []
        
        # State
        self.last_check_time = None
        self.empty_areas = []
        self.total_alerts_sent = 0
        self.last_alert_time = None
        self.last_image_path = None
        
        # State persistence and locking
        self.state_dir = os.path.join(os.path.expanduser("~"), ".stock-alert")
        self.state_file = os.path.join(self.state_dir, f"{name}.json")
        self.lock_file = os.path.join(self.state_dir, f"{name}.lock")
        self.images_dir = os.path.join(self.state_dir, "images")
        os.makedirs(self.state_dir, exist_ok=True)
        os.makedirs(self.images_dir, exist_ok=True)
        
        # Background task
        self._check_task = None
        
        # Load state silently
        self._load_state_silent()
    
    def _load_state_silent(self):
        """Load state quietly without logging at initialization time."""
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
                    self.empty_areas = state.get("empty_areas", [])
                    self.last_image_path = state.get("last_image_path", None)
            except Exception:
                pass  # Silently fail on error
    
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
                            self.last_image_path = state.get("last_image_path")
                            
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
                        "empty_areas": self.empty_areas,
                        "last_image_path": self.last_image_path
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
        
        # Check camera configuration if enabled
        include_image = attributes.get("include_image", False)
        if include_image and not attributes.get("camera_name"):
            raise ValueError("camera_name must be specified when include_image is true")
        
        # Return required dependencies
        deps = ["remote-1:langer_fill", "email"]
        
        # Add camera dependency if configured
        if include_image and attributes.get("camera_name"):
            camera_name = attributes.get("camera_name")
            # If camera_name includes a remote name, use it directly
            if ":" in camera_name:
                deps.append(camera_name)
            else:
                # Otherwise, assume it's on remote-1
                deps.append(f"remote-1:{camera_name}")
        
        LOGGER.info(f"StockAlertEmail.validate_config returning dependencies: {deps}")
        return deps
    
    def reconfigure(self, config: ComponentConfig, dependencies: Mapping[str, ResourceBase]):
        """Configure the stock alert with updated settings."""
        # Use a lock to ensure only one instance is fully active and logging
        lock = fasteners.InterProcessLock(self.lock_file)
        is_primary = lock.acquire(blocking=False)
        
        # Silent early configuration - no logging yet
        self.location = config.attributes.fields["location"].string_value
        attributes = struct_to_dict(config.attributes)
        
        # Configure alert settings
        self.recipients = attributes.get("recipients", [])
        self.areas = attributes.get("areas", [])
        self.descriptor = attributes.get("descriptor", "Areas of Interest")
        
        # Camera configuration
        self.include_image = attributes.get("include_image", False)
        if isinstance(self.include_image, str):
            self.include_image = self.include_image.lower() == "true"
            
        self.camera_name = attributes.get("camera_name", "")
        self.image_width = int(attributes.get("image_width", 640))
        self.image_height = int(attributes.get("image_height", 480))
        
        # Simplified scheduling configuration
        self.weekdays_only = attributes.get("weekdays_only", True)
        if isinstance(self.weekdays_only, str):
            self.weekdays_only = self.weekdays_only.lower() == "true"
        
        # Direct check times configuration
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
            except Exception:
                # No logging yet
                pass
        
        # Sort and deduplicate
        self.check_times = sorted(list(set(self.check_times)))
        
        # Store dependencies
        self.dependencies = dependencies
        
        # Cancel existing task if it exists
        if self._check_task and not self._check_task.done():
            self._check_task.cancel()
        
        if is_primary:
            # Only log configuration details in the primary instance
            LOGGER.info(f"Configured {self.name} for location '{self.location}'")
            LOGGER.info(f"Weekdays only: {self.weekdays_only}")
            LOGGER.info(f"Check times: {', '.join(self.check_times)}")
            LOGGER.info(f"Monitoring areas: {', '.join(self.areas)}")
            LOGGER.info(f"Will send alerts to: {', '.join(self.recipients)}")
            
            if self.include_image:
                LOGGER.info(f"Will include images from camera: {self.camera_name}")
            else:
                LOGGER.info("Image capture disabled")
                
            # Start main check loop and pass lock
            self._check_task = asyncio.create_task(self._run_checks_primary(lock))
        else:
            # For secondary instances, run a dummy task
            self._check_task = asyncio.create_task(self._run_checks_secondary())
    
    async def _run_checks_primary(self, lock):
        """Run the monitoring loop as the primary instance (with the lock)."""
        LOGGER.info(f"Starting scheduled checks loop for {self.name} (PID: {os.getpid()})")
        
        try:
            # Now we can properly log state since we're the primary
            if os.path.exists(self.state_file):
                try:
                    with open(self.state_file, "r") as f:
                        state = json.load(f)
                        # Load state attributes...
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
                except Exception as e:
                    LOGGER.error(f"Error loading state: {e}")
            else:
                LOGGER.info(f"No state file at {self.state_file}, starting fresh")
            
            # Primary check loop
            while True:
                # Get current time and next check time
                current_time = datetime.datetime.now()
                next_check = self._get_next_check_time(current_time)
                
                if next_check is None:
                    # No check times configured, sleep for 1 hour and retry
                    LOGGER.warning(f"No check times configured for {self.name}, sleeping for 1 hour")
                    await asyncio.sleep(3600)
                    continue
                
                # Calculate sleep time
                sleep_seconds = (next_check - current_time).total_seconds()
                
                if sleep_seconds <= 0:
                    # We're already past the check time, so run now
                    LOGGER.info(f"Already past scheduled check time {next_check}, running now")
                    await self.perform_check()
                    # Small gap between checks
                    await asyncio.sleep(1)
                    continue
                
                # Log next check information
                LOGGER.info(f"Next check scheduled for {next_check.strftime('%H:%M')} (sleeping {sleep_seconds:.1f} seconds)")
                
                try:
                    # Sleep until the exact next check time
                    await asyncio.sleep(sleep_seconds)
                    
                    # Perform the check
                    await self.perform_check()
                    
                except asyncio.CancelledError:
                    LOGGER.info(f"Sleep interrupted for {self.name}")
                    raise
            
        except asyncio.CancelledError:
            LOGGER.info(f"Check loop cancelled for {self.name}")
            raise
        except Exception as e:
            LOGGER.error(f"Error in check loop: {e}")
            await asyncio.sleep(60)
        finally:
            try:
                lock.release()
                LOGGER.info(f"Released lock for {self.name}, check loop exiting (PID: {os.getpid()})")
            except Exception as e:
                LOGGER.error(f"Error releasing lock: {e}")

    async def _run_checks_secondary(self):
        """Secondary instance just sleeps indefinitely."""
        try:
            # Just wait quietly until cancelled
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            # Exit silently
            pass
    
    async def get_readings(self, *, extra: Optional[Mapping[str, Any]] = None, timeout: Optional[float] = None, **kwargs) -> Mapping[str, SensorReading]:
        """Get current sensor readings."""
        current_time = datetime.datetime.now()
        
        # Calculate next check time
        next_check = self._get_next_check_time(current_time)
        
        readings = {
            "empty_areas": self.empty_areas,
            "location": self.location,
            "last_check_time": str(self.last_check_time) if self.last_check_time else "never",
            "next_check_time": str(next_check) if next_check else "none scheduled",
            "total_alerts_sent": self.total_alerts_sent,
            "last_alert_time": str(self.last_alert_time) if self.last_alert_time else "never",
            "weekdays_only": self.weekdays_only,
            "check_times": self.check_times,
            "areas_monitored": self.areas,
            "include_image": self.include_image
        }
        
        if self.include_image:
            readings["camera_name"] = self.camera_name
            readings["last_image_path"] = self.last_image_path
        
        return readings
    
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
    
    async def capture_image(self) -> Optional[Dict[str, Any]]:
        """Capture an image from the camera and save it to disk."""
        if not self.include_image or not self.camera_name:
            return None
        
        # Find camera in dependencies with improved matching
        camera = None
        camera_name = self.camera_name.lower()  # Normalize to lowercase for comparison
        
        for name, resource in self.dependencies.items():
            resource_name = str(name).lower()  # Normalize to lowercase
            
            # Check if this is a Camera resource
            if isinstance(resource, Camera):
                # Direct match
                if resource_name == camera_name:
                    camera = resource
                    LOGGER.info(f"Found camera with direct match: {name}")
                    break
                # Match with remote prefix
                elif resource_name.endswith(f":{camera_name}"):
                    camera = resource
                    LOGGER.info(f"Found camera with remote prefix: {name}")
                    break
                # Match camera name as the last part of resource name
                elif resource_name.split(":")[-1] == camera_name:
                    camera = resource
                    LOGGER.info(f"Found camera from last part of name: {name}")
                    break
                # Partial match (in case of different naming conventions)
                elif camera_name in resource_name:
                    camera = resource
                    LOGGER.info(f"Found camera with partial match: {name}")
                    break
        
        if not camera:
            LOGGER.warning(f"Camera '{self.camera_name}' not found in dependencies")
            return None
        
        try:
            # Capture image
            LOGGER.info(f"Capturing image from camera '{self.camera_name}'")
            image = await camera.get_image(mime_type="image/jpeg")
            
            # Add detailed debug logging about the image object
            LOGGER.info(f"Image type: {type(image)}")
            if hasattr(image, '__dict__'):
                LOGGER.info(f"Image attributes: {list(image.__dict__.keys())}")
            elif isinstance(image, dict):
                LOGGER.info(f"Image dict keys: {list(image.keys())}")
            
            # Log available methods
            methods = [method for method in dir(image) if callable(getattr(image, method)) and not method.startswith('_')]
            LOGGER.info(f"Available methods: {methods}")
            
            # Get the image data and handle various possible return types
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{timestamp}_{self.name}.jpg"
            image_path = os.path.join(self.images_dir, filename)
            
            # Handle different possible return types from get_image
            if isinstance(image, ViamImage):
                # For ViamImage objects, get the bytes
                try:
                    # If there's a get_bytes method
                    if hasattr(image, 'get_bytes'):
                        image_data = await image.get_bytes()
                    # If there's a bytes attribute
                    elif hasattr(image, 'bytes'):
                        image_data = image.bytes
                    # Or try to access the raw data directly as a fallback
                    elif hasattr(image, 'data'):
                        image_data = image.data
                    else:
                        # If we can't access the bytes, try converting to bytes
                        if hasattr(image, 'to_bytes'):
                            image_data = await image.to_bytes()
                        else:
                            raise AttributeError("Cannot access image data")
                except Exception as e:
                    LOGGER.error(f"Error accessing image data from ViamImage: {e}")
                    return None
            # Handle direct bytes return
            elif isinstance(image, bytes):
                image_data = image
            # Handle possible dict return with bytes inside
            elif isinstance(image, dict) and 'data' in image:
                image_data = image['data']
            # Unknown format
            else:
                LOGGER.warning(f"Unexpected image type: {type(image)}")
                return None
                
            # Save image data to disk
            try:
                with open(image_path, "wb") as f:
                    f.write(image_data)
                self.last_image_path = image_path
                LOGGER.info(f"Saved image to {image_path}")
                
                # Return information about the image
                return {
                    "path": image_path,
                    "timestamp": timestamp,
                    "mime_type": "image/jpeg"
                }
            except Exception as e:
                LOGGER.error(f"Error saving image to file: {e}")
                return None
                
        except Exception as e:
            LOGGER.error(f"Error capturing image: {e}")
            return None
    
    async def send_alert(self, empty_areas: List[str]):
        """Send email alert for empty areas with optional image attachment."""
        # Find email service
        email_service = None
        for name, resource in self.dependencies.items():
            if isinstance(resource, Generic) and str(name).endswith("email"):
                email_service = resource
                break
        
        # Update state
        self.last_alert_time = datetime.datetime.now()
        self.total_alerts_sent += 1
        
        # Capture image if enabled
        image_info = None
        if self.include_image:
            image_info = await self.capture_image()
        
        # Save state after image capture
        self._save_state()
        
        # If no email service, just log
        if not email_service:
            LOGGER.warning(f"No email service available for {self.name}")
            return
        
        # Send the email
        try:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # Sort empty areas alphabetically and numerically
            sorted_areas = sorted(empty_areas, key=lambda x: (x.split('-')[0], int(x.split('-')[1]) if len(x.split('-')) > 1 and x.split('-')[1].isdigit() else x.split('-')[1]))
            
            # Format the subject with location at the end
            subject = f"Empty {self.descriptor}: {', '.join(sorted_areas)} - {self.location}"
            
            # Create the email body
            body = f"""The following {self.descriptor.lower()} are empty and need attention: {', '.join(sorted_areas)}

    Location: {self.location}
    Time: {timestamp}"""
            
            # For HTML email
            html_body = f"""<html>
    <body>
    <p>The following {self.descriptor.lower()} are empty and need attention: {', '.join(sorted_areas)}</p>
    <p>Location: {self.location}<br>
    Time: {timestamp}</p>
    """
            
            # Set up the email command - start with simple version
            email_cmd = {
                "command": "send",
                "to": self.recipients,
                "subject": subject,
                "body": body  # Plain text version
            }
            
            # Add image if available - SIMPLIFIED APPROACH using direct attachment
            if image_info and os.path.exists(image_info["path"]):
                try:
                    # Read image file - binary mode
                    with open(image_info["path"], "rb") as f:
                        image_data = f.read()
                    
                    # Verify image data is valid
                    if not image_data or len(image_data) < 100:
                        LOGGER.warning(f"Image file is too small or empty: {image_info['path']}")
                    else:
                        # Encode as base64
                        encoded_image = base64.b64encode(image_data).decode("utf-8")
                        
                        # Log information
                        LOGGER.info(f"Adding image: {os.path.basename(image_info['path'])}, size: {len(image_data)} bytes")
                        
                        # Update HTML with the image embedded
                        html_body += f"""<p>A snapshot of the area is shown below:</p>
    <img src="data:image/jpeg;base64,{encoded_image}" alt="Empty area snapshot" style="max-width: 100%;">
    </body>
    </html>"""
                        
                        # Set the HTML version of the email
                        email_cmd["html"] = html_body
                        
                        # Update text body
                        email_cmd["body"] += "\n\nA snapshot of the area is included in this email."
                except Exception as e:
                    LOGGER.error(f"Error preparing image: {e}")
                    # Complete the HTML even if there's an error
                    html_body += "</body></html>"
                    email_cmd["html"] = html_body
            else:
                # Complete the HTML even without an image
                html_body += "</body></html>"
                email_cmd["html"] = html_body
            
            # Log the email command (truncate the base64 data)
            log_cmd = {k: v for k, v in email_cmd.items() if k != "html"}
            log_cmd["html"] = "HTML content with embedded image (truncated)"
            LOGGER.info(f"Email command structure: {json.dumps(log_cmd)}")
            
            # Send the email
            result = await email_service.do_command(email_cmd)
            LOGGER.info(f"Email service response: {result}")
            LOGGER.info(f"Sent email alert to {len(self.recipients)} recipients")
        except Exception as e:
            LOGGER.error(f"Failed to send email alert: {e}")
            LOGGER.error(f"Exception type: {type(e)}")
            if hasattr(e, "__traceback__"):
                import traceback
                tb_str = ''.join(traceback.format_exception(type(e), e, e.__traceback__))
                LOGGER.error(f"Traceback: {tb_str}")
    
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
                "next_check_time": str(next_check) if next_check else "none scheduled"
            }
        
        elif cmd == "capture_image":
            # Force an image capture
            if not self.include_image:
                return {
                    "status": "error",
                    "message": "Image capture not enabled"
                }
            
            image_info = await self.capture_image()
            if image_info:
                return {
                    "status": "completed",
                    "image_path": image_info["path"],
                    "timestamp": image_info["timestamp"]
                }
            else:
                return {
                    "status": "error",
                    "message": "Failed to capture image"
                }
        
        else:
            return {
                "status": "error",
                "message": f"Unknown command: {cmd}"
            }