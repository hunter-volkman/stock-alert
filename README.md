# Module stock-alert 

This module monitors stock levels using a sensor (e.g., `langer_fill`) and sends email alerts when specified areas of interest are empty. It is designed for environments like retail, with configurable check times, weekday-only operation by default, and optional camera image capture to include in alerts.

## Model hunter:stock-alert:email

Sends email alerts via SendGrid when configured areas of interest have zero stock levels, with optional image attachments from a camera. Alerts are scheduled at specific times, and the module maintains state persistence across restarts.

### Configuration

```json
{
  "location": "389 5th Ave, New York, NY",
  "recipients": ["hunter.volkman@viam.com"],
  "areas": ["A-1", "A-2", "A-3"],
  "descriptor": "Shelves",
  "weekdays_only": true,
  "check_times": ["08:15", "08:30", "10:15", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30", "14:00", "14:30", "15:00"],
  "include_image": true,
  "camera_name": "remote-1:ffmpeg",
  "image_width": 640,
  "image_height": 480,
  "sendgrid_api_key": "<sendgrid-api-key>",
  "sender_email": "no-reply@viam.com",
  "sender_name": "Stock Alert System"
}
```

#### Attributes

| Name          | Type   | Inclusion | Description                |
|---------------|--------|-----------|----------------------------|
| `location` | string  | Required  | The physical location being monitored. |
| `recipients` | list[str] | Required  | List of email addresses to receive email alerts. |
| `areas` | list[str] | Required  | List of specific area identifiers to monitor (e.g., "A-1"). |
| `descriptor` | string | Optional  | Descriptor for areas in alerts (e.g., "Shelves"). Default: "Areas of Interest". |
| `weekdays_only` | bool/string | Optional  | Only run checks on weekdays (Mon-Fri). Default: `true`. |
| `check_times` | string | Optional  | Specific times (HH:MM) to check stock levels, sorted chronologically. Default: ["08:15", "08:30", "10:15", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30", "14:00", "14:30", "15:00"]. |
| `inclue_image` | bool/string | Optional  | Whether to include a camera snapshot in alerts. Default: `false`. |
| `camera_name` | string | Optional  | Name of the camera component to capture images (required if `include_image` is true). |
| `image_width` | int | Optional  | Width of captured images in pixels. Default: 640. |
| `image_height` | int | Optional  | Height of captured images in pixels. Default: 480. |
| `sendgrid_api_key` | string | string  | API key for SendGrid to send emails. |
| `sendgrid_api_key` | string | string  | API key for SendGrid to send emails. |
| `sender_email` | string | string  | Email address of the sender. Default: "no-reply@viam.com". |
| `sender_name` | string | string  | Name of the sender. Default: "Stock Alert System". |




#### Example Configuration

```json
{
  "name": "langer_alert",
  "type": "sensor",
  "model": "hunter:stock-alert:email",
  "attributes": {
    "location": "389 5th Ave, New York, NY",
    "recipients": ["hunter.volkman@viam.com"],
    "areas": ["A-1", "A-2", "A-3", "B-1", "B-2", "B-3", "C-1", "C-2", "C-3", "D-1", "D-2", "D-3"],
    "descriptor": "Shelves",
    "weekdays_only": true,
    "check_times": ["08:15", "08:30", "10:15", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30", "14:00", "14:30", "15:00"],
    "include_image": true,
    "camera_name": "remote-1:ffmpeg",
    "image_width": 640,
    "image_height": 480,
    "sendgrid_api_key": "your-sendgrid-api-key",
    "sender_email": "no-reply@viam.com",
    "sender_name": "Stock Alert System"
  },
  "depends_on": ["remote-1:langer_fill"]
}
```

#### Dependencies
* `langer_fill`: Sensor component (`viam-soleng:stock-fill:fill-percent`).
* `camera` (optional): Camera component (e.g., `remote-1:ffmpeg`) for capturing images if include_image is enabled.


#### Usage
1. Configure the email model with your location, recipients, areas, and check times.
2. Ensure the langer_fill sensor and, if enabled, a camera component are available.
3. Provide a valid SendGrid API key for email functionality.
4. The module will check stock levels at the specified check_times and send alerts for empty areas, optionally including camera snapshots.

### DoCommand

The email alert model supports the following commands:

#### Check Now

Forces an immediate check for empty areas.

```json
{
  "command": "check_now"
}
```

Returns:

```json
{
  "status": "completed",
  "empty_areas": ["A-1", "A-3"]
}
```

#### Get Schedule

Returns the current schedule configuration.

```json
{
  "command": "get_schedule"
}
```

Returns:

```json
{
  "status": "completed",
  "weekdays_only": true,
  "check_times": ["08:15", "08:30", "10:15", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30", "14:00", "14:30", "15:00"],
  "next_check_time": "2025-04-05 08:15:00"
}
```

#### Capture Image

Forces an image capture from the camera (if enabled).

```json
{
  "command": "capture_image"
}
```

Returns:

```json
{
  "status": "completed",
  "image_path": "/home/hunter.volkman/.stock-alert/images/20250405_153500_langer_alert.jpg",
  "timestamp": "20250405_153500"
}
```

or, on failure:

```json
{
  "status": "error",
  "message": "Failed to capture image"
}
```

#### Test Email

Sends a test email to verify email configuration.

```json
{
  "command": "test_email"
}
```

Returns:

```json
{
  "status": "completed",
  "message": "Test email sent with status code 202",
  "recipients": ["hunter.volkman@viam.com"]
}
```

or, on failure:

```json
{
  "status": "error",
  "message": "Failed to send test email: No SendGrid API key configured"
}
```


### Sensor Readings

The module provides comprehensive readings that can be used for monitoring:

```json
{
  "empty_areas": ["A-1", "A-3"],
  "location": "389 5th Ave, New York, NY",
  "last_check_time": "2025-04-05 15:35:00",
  "next_check_time": "2025-04-05 15:40:00",
  "total_alerts_sent": 5,
  "last_alert_time": "2025-04-05 15:30:00",
  "weekdays_only": true,
  "check_times": ["08:15", "08:30", "10:15", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30", "14:00", "14:30", "15:00"],
  "areas_monitored": ["A-1", "A-2", "A-3"],
  "include_image": true,
  "camera_name": "remote-1:ffmpeg",
  "last_image_path": "/home/hunter.volkman/.stock-alert/images/20250405_153500_langer_alert.jpg",
  "pid": 12345
}
```

### Implementation Details
* Scheduling: Checks occur at specific times (check_times) with support for weekday-only operation.
* State Persistence: Uses fasteners for inter-process locking and saves state to ~/.stock-alert/*.json files, including last check times, alert history, and image paths.
* Image Capture: Supports capturing images from Viam cameras (e.g., remote-1:ffmpeg) and attaching them to emails if include_image is enabled.
* Email Alerts: Uses SendGrid for sending emails, requiring a valid API key.
* Dependency Handling: Robustly manages dependencies like sensors and cameras, even with complex resource names.


### Known Issues and Debugging

* Email Sending Error: The error Please use a To, From, Cc or Bcc object. in alert.py:543 indicates an issue with how email recipients are added using the SendGrid library. Ensure that each recipient in self.recipients is a valid email string, and the Email object is correctly instantiated in send_alert. Update the send_alert method to verify that self.recipients contains valid email addresses before adding them:

```python
for recipient in self.recipients:
    if not isinstance(recipient, str) or '@' not in recipient:
        LOGGER.error(f"Invalid recipient email: {recipient}")
        continue
    message.add_to(Email(recipient))
```

* Image Type Warning: The warning Unsupported image type: <class 'viam.media.video.ViamImage'> suggests that the image returned by the camera (await camera.get_image) might not be handled correctly. Ensure the capture_image method correctly processes ViamImage objects. Update the method to explicitly handle ViamImage:

```python
if isinstance(image, ViamImage):
    image_data = await image.get_bytes()
```

* State and Locking: The state persistence uses file locking with fasteners, which should prevent race conditions, but ensure the lock files (*.lock) are not corrupted or inaccessible.

* Dependencies: Verify that the langer_fill sensor and camera (if used) are correctly configured and accessible in the Viam robot configuration.

For further debugging, check the SendGrid API key, ensure the camera is returning the expected image format, and review the `dependencies` mapping in `reconfigure` to ensure all required components are present.