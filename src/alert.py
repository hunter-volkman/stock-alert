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
from viam.proto.app.robot import ComponentConfig
from viam.resource.base import ResourceBase
from viam.resource.types import Model, ModelFamily
from viam.utils import SensorReading, struct_to_dict
from viam.logging import getLogger
from viam.media.video import ViamImage
from PIL import Image
from io import BytesIO

# Add SendGrid imports
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (
    Mail, Attachment, FileContent, FileName, 
    FileType, Disposition, ContentId, Email, Content
)

LOGGER = getLogger(__name__)

class StockAlertEmail(Sensor):
    MODEL = Model(ModelFamily("hunter", "stock-alert"), "email")
    
    @classmethod
    def new(cls, config: ComponentConfig, dependencies: Mapping[str, ResourceBase]) -> "StockAlertEmail":
        """Create a new StockAlertEmail instance."""
        instance = cls(config.name)
        instance.reconfigure(config, dependencies)
        return instance
    
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
        
        # Check SendGrid API key
        sendgrid_api_key = attributes.get("sendgrid_api_key", "")
        if not sendgrid_api_key:
            LOGGER.warning("No SendGrid API key provided in configuration")
        
        # Check camera configuration if enabled
        include_image = attributes.get("include_image", False)
        if include_image and not attributes.get("camera_name"):
            raise ValueError("camera_name must be specified when include_image is true")
        
        # Return required dependencies
        deps = ["remote-1:langer_fill"]
        
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
    
    def __init__(self, name: str):
        super().__init__(name)
        self.dependencies = {}
        self.config = None
        self.location = ""
        self.descriptor = "Areas of Interest"
        self.areas = []
        self.recipients = []
        
        # Email configuration
        self.sendgrid_api_key = ""
        self.sender_email = "no-reply@viam.com"
        self.sender_name = "Stock Alert System"
        
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
        
        # State persistence
        self.state_dir = os.path.join(os.path.expanduser("~"), ".stock-alert")
        self.state_file = os.path.join(self.state_dir, f"{name}.json")
        self.images_dir = os.path.join(self.state_dir, "images")
        os.makedirs(self.state_dir, exist_ok=True)
        os.makedirs(self.images_dir, exist_ok=True)
        
        # Background task
        self._check_task = None
        
        # Load state silently
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
                            self.last_image_path = state.get("last_image_path")
                            
                        LOGGER.info(f"Loaded state from {self.state_file}")
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
    
    def reconfigure(self, config: ComponentConfig, dependencies: Mapping[str, ResourceBase]):
        """Configure the stock alert with updated settings."""
        # Store config for later use
        self.config = config
        
        # Configure from attributes
        self.location = config.attributes.fields["location"].string_value
        attributes = struct_to_dict(config.attributes)
        
        # Configure alert settings
        self.recipients = attributes.get("recipients", [])
        self.areas = attributes.get("areas", [])
        self.descriptor = attributes.get("descriptor", "Areas of Interest")
        
        # Email configuration
        self.sender_email = attributes.get("sender_email", "no-reply@viam.com")
        self.sender_name = attributes.get("sender_name", "Stock Alert System")
        self.sendgrid_api_key = attributes.get("sendgrid_api_key", "")
        
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
        self.check_times = attributes.get("check_times", ["08:15", "08:30", "10:15", "10:30", "11:00", 
                                                      "11:30", "12:00", "12:30", "13:00", "13:30", 
                                                      "14:00", "14:30", "15:00"])

        # Sort the check times to ensure they're in chronological order
        self.check_times = sorted(list(set(self.check_times)))
        
        # Store dependencies
        self.dependencies = dependencies
        
        # Cancel existing task if it exists
        if self._check_task and not self._check_task.done():
            self._check_task.cancel()
        
        # Log configuration details
        LOGGER.info(f"Configured {self.name} for location '{self.location}'")
        LOGGER.info(f"Weekdays only: {self.weekdays_only}")
        LOGGER.info(f"Check times: {', '.join(self.check_times)}")
        LOGGER.info(f"Monitoring areas: {', '.join(self.areas)}")
        LOGGER.info(f"Will send alerts to: {', '.join(self.recipients)}")
        
        if self.sendgrid_api_key:
            LOGGER.info("SendGrid API key configured")
        else:
            LOGGER.warning("No SendGrid API key configured")
            
        if self.include_image:
            LOGGER.info(f"Will include images from camera: {self.camera_name}")
        else:
            LOGGER.info("Image capture disabled")
            
        # Start main check loop
        self._check_task = asyncio.create_task(self._run_checks())
    
    async def _run_checks(self):
        """Run the monitoring loop."""
        LOGGER.info(f"Starting scheduled checks loop for {self.name} (PID: {os.getpid()})")
        
        try:
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
                    LOGGER.info(f"Already past scheduled check time {next_check.strftime('%H:%M')}, running now")
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
            await asyncio.sleep(60)  # Wait before restarting
    
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
            "include_image": self.include_image,
            "pid": os.getpid()
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
    
    async def capture_image(self) -> Optional[Dict[str, Any]]:
        """Capture an image from the camera and save it to disk."""
        if not self.include_image or not self.camera_name:
            return None
        
        # Find camera in dependencies
        camera = None
        for name, resource in self.dependencies.items():
            if isinstance(resource, Camera):
                # Check if the camera name is in the resource name
                if self.camera_name.lower() in str(name).lower():
                    camera = resource
                    LOGGER.info(f"Found camera: {name}")
                    break
        
        if not camera:
            LOGGER.warning(f"Camera '{self.camera_name}' not found in dependencies")
            return None
        
        try:
            # Capture image
            LOGGER.info(f"Capturing image from camera '{self.camera_name}'")
            image = await camera.get_image(mime_type="image/jpeg")
            
            # Get the image data
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{timestamp}_{self.name}.jpg"
            image_path = os.path.join(self.images_dir, filename)
            
            # Handle the image data depending on its type
            if isinstance(image, ViamImage):
                # Use PIL to process ViamImage.data, inspired by image-emailer
                pil_image = Image.open(BytesIO(image.data))
                pil_image.save(image_path, "JPEG")
            elif isinstance(image, bytes):
                # Direct bytes
                with open(image_path, "wb") as f:
                    f.write(image)
            elif isinstance(image, dict) and 'data' in image:
                # Dict with data
                with open(image_path, "wb") as f:
                    f.write(image['data'])
            else:
                LOGGER.warning(f"Unsupported image type: {type(image)}")
                return None
                
            self.last_image_path = image_path
            LOGGER.info(f"Saved image to {image_path}")
            
            return {
                "path": image_path,
                "timestamp": timestamp,
                "mime_type": "image/jpeg"
            }
                
        except Exception as e:
            LOGGER.error(f"Error capturing image: {e}")
            return None
    
    async def send_alert(self, empty_areas: List[str]):
        """Send email alert for empty areas with optional image attachment using SendGrid directly."""
        if not self.sendgrid_api_key:
            LOGGER.error("No SendGrid API key configured, cannot send alert")
            return
            
        # Update state
        self.last_alert_time = datetime.datetime.now()
        self.total_alerts_sent += 1
        
        # Capture image if enabled
        image_info = None
        if self.include_image:
            image_info = await self.capture_image()
        
        # Save state after image capture
        self._save_state()
        
        try:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # Sort empty areas alphabetically and numerically
            sorted_areas = sorted(empty_areas, key=lambda x: (
                x.split('-')[0], 
                int(x.split('-')[1]) if len(x.split('-')) > 1 and x.split('-')[1].isdigit() else x.split('-')[1]
            ))
            
            # Format the subject with location at the end
            subject = f"Empty {self.descriptor}: {', '.join(sorted_areas)} - {self.location}"
            
            # Create the email body
            body_text = f"""The following {self.descriptor.lower()} are empty and need attention: {', '.join(sorted_areas)}

Location: {self.location}
Time: {timestamp}"""
            
            # Create email message with to_emails directly, inspired by viam-sendgrid-email
            valid_recipients = []
            for recipient in self.recipients:
                if not isinstance(recipient, str) or '@' not in recipient:
                    LOGGER.error(f"Invalid recipient email: {recipient}")
                    continue
                valid_recipients.append(recipient)
            
            if not valid_recipients:
                LOGGER.error("No valid recipients found, aborting send")
                return
            
            message = Mail(
                from_email=Email(self.sender_email, self.sender_name),
                to_emails=valid_recipients,  # Pass list directly instead of add_to
                subject=subject,
                plain_text_content=Content("text/plain", body_text)  # Add content directly
            )
            
            # Create HTML content
            html_content = f"""<html>
<body>
<p>The following {self.descriptor.lower()} are empty and need attention: {', '.join(sorted_areas)}</p>
<p>Location: {self.location}<br>
Time: {timestamp}</p>
"""
            
            # Add image if available
            if image_info and os.path.exists(image_info["path"]):
                try:
                    # Read image file
                    with open(image_info["path"], "rb") as f:
                        file_content = base64.b64encode(f.read()).decode()
                    
                    file_name = os.path.basename(image_info["path"])
                    
                    # Add the attachment
                    attachment = Attachment()
                    attachment.file_content = FileContent(file_content)
                    attachment.file_name = FileName(file_name)
                    attachment.file_type = FileType("image/jpeg")
                    attachment.disposition = Disposition("attachment")
                    
                    # Add attachment to the message
                    message.add_attachment(attachment)
                    
                    # Add reference to HTML
                    html_content += f"""<p>A snapshot of the area is attached to this email.</p>"""
                    
                    LOGGER.info(f"Added image attachment: {file_name}")
                except Exception as e:
                    LOGGER.error(f"Error attaching image: {e}")
            
            # Complete HTML
            html_content += "</body></html>"
            message.add_content(Content("text/html", html_content))
            
            # Send the email
            sg = SendGridAPIClient(self.sendgrid_api_key)
            response = sg.send(message)
            LOGGER.info(f"Email sent via SendGrid API. Status code: {response.status_code}")
            LOGGER.info(f"Sent email alert to {len(valid_recipients)} recipients")
            
        except Exception as e:
            LOGGER.error(f"Failed to send email alert: {e}")
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
                
        elif cmd == "test_email":
            # Send a test email
            if not self.sendgrid_api_key:
                return {
                    "status": "error",
                    "message": "No SendGrid API key configured"
                }
                
            try:
                # Create test email content
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                subject = f"Test Alert from {self.location}"
                body = f"This is a test alert from {self.name} at {self.location}.\nTime: {timestamp}"
                
                # Create email message with to_emails directly
                valid_recipients = [r for r in self.recipients if isinstance(r, str) and '@' in r]
                if not valid_recipients:
                    return {
                        "status": "error",
                        "message": "No valid recipients found"
                    }
                
                message = Mail(
                    from_email=Email(self.sender_email, self.sender_name),
                    to_emails=valid_recipients,
                    subject=subject,
                    plain_text_content=Content("text/plain", body)
                )
                
                # Use a raw string to avoid backslash issues with the newline replacement
                html_body = body.replace("\n", "<br>")
                message.add_content(Content("text/html", f"<html><body><p>{html_body}</p></body></html>"))
                
                # Send email
                sg = SendGridAPIClient(self.sendgrid_api_key)
                response = sg.send(message)
                
                return {
                    "status": "completed",
                    "message": f"Test email sent with status code {response.status_code}",
                    "recipients": self.recipients
                }
            except Exception as e:
                return {
                    "status": "error",
                    "message": f"Failed to send test email: {str(e)}"
                }
        else:
            return {
                "status": "error",
                "message": f"Unknown command: {cmd}"
            }