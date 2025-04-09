# Module stock-alert 

This module monitors stock levels using a sensor (e.g., `langer_fill`) and sends email alerts when specified areas of interest are empty. It is designed for environments like retail, with configurable check times, weekday-only operation by default, and optional camera image capture to include in alerts.

## Model hunter:stock-alert:email

Sends email alerts via SendGrid when configured areas of interest have insufficient stock levels (based on percentile calculations), with optional image attachments from a camera. Alerts are scheduled at specific times, and the module maintains state persistence across restarts.

### Configuration

```json
{
  "location": "389 5th Ave, New York, NY",
  "recipients": ["example@viam.com"],
  "areas": ["A-1", "A-2", "A-3"],
  "descriptor": "Shelves",
  "weekdays_only": true,
  "check_times": ["08:15", "08:30", "10:15", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30", "14:00", "14:30", "15:00"],
  "empty_threshold": 0.0,
  "sampling_window_minutes": 5,
  "sampling_interval_seconds": 1,
  "include_image": true,
  "camera_name": "ffmpeg",
  "image_width": 640,
  "image_height": 480,
  "sendgrid_api_key": "<sendgrid-api-key>",
  "sender_email": "no-reply@viam.com",
  "sender_name": "Stock Alert Module"
}
```

#### Attributes

| Name          | Type   | Inclusion | Description                |
|---------------|--------|-----------|----------------------------|
| `location` | string  | Required  | The physical location being monitored. |
| `recipients` | list[str] | Required  | List of email addresses to receive email alerts. |
| `areas` | list[str] | Required  | List of specific area identifiers to monitor (e.g., "A-1"). |
| `descriptor` | string | Optional  | Descriptor for areas in alerts (e.g., "Shelves"). Default: "Areas of Interest". |
| `weekdays_only` | bool | Optional  | Only run checks on weekdays (Mon-Fri). Default: `true`. |
| `check_times` | string | Optional  | Specific times (HH:MM) to check stock levels, sorted chronologically. Default: ["08:15", "08:30", "10:15", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30", "14:00", "14:30", "15:00"]. |
| `empty_threshold` | float | Optional  | Threshold below which an area is considered empty. Default: 0.0. |
| `sampling_window_minutes` | int | Optional  | Minutes to collect samples for percentile calculation. Default: 5. |
| `sampling_interval_seconds` | int | Optional  | Seconds between samples. Default: 1. |
| `include_image` | bool | Optional  | Whether to include a camera snapshot in alerts. Default: `false`. |
| `camera_name` | string | Optional  | Name of the camera component to capture images (required if `include_image` is true). |
| `image_width` | int | Optional  | Width of captured images in pixels. Default: 640. |
| `image_height` | int | Optional  | Height of captured images in pixels. Default: 480. |
| `sendgrid_api_key` | string | string  | API key for SendGrid to send emails. |
| `sender_email` | string | string  | Email address of the sender. Default: "no-reply@viam.com". |
| `sender_name` | string | string  | Name of the sender. Default: "Stock Alert Module". |




#### Example Configuration

```json
{
  "name": "langer_alert",
  "type": "sensor",
  "model": "hunter:stock-alert:email",
  "attributes": {
    "location": "389 5th Ave, New York, NY",
    "recipients": ["example@viam.com"],
    "areas": ["A-1", "A-2", "A-3", "B-1", "B-2", "B-3", "C-1", "C-2", "C-3", "D-1", "D-2", "D-3"],
    "descriptor": "Shelves",
    "weekdays_only": true,
    "check_times": ["08:15", "08:30", "10:15", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30", "14:00", "14:30", "15:00"],
    "empty_threshold": 0.0,
    "sampling_window_minutes": 5,
    "sampling_interval_seconds": 1,
    "include_image": true,
    "camera_name": "ffmpeg",
    "image_width": 640,
    "image_height": 480,
    "sendgrid_api_key": "your-sendgrid-api-key",
    "sender_email": "no-reply@viam.com",
    "sender_name": "Stock Alert Module"
  },
  "depends_on": ["langer_fill"]
}
```

#### Dependencies
* `langer_fill`: Sensor component (typically viam-soleng:stock-fill:fill-percent).
* `camera` (optional): Camera component (e.g., ffmpeg) for capturing images if `include_image` is enabled.


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
  "empty_areas": ["A-1", "A-3"],
  "percentiles": {"A-1": 0.0, "A-2": 45.2, "A-3": 0.0}
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
  "next_check_time": "2025-04-07 08:15:00"
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
  "image_path": "/home/user/.stock-alert/images/20250407_153500_langer_alert.jpg",
  "timestamp": "20250407_153500"
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
  "recipients": ["example@viam.com"]
}
```

or, on failure:

```json
{
  "status": "error",
  "message": "Failed to send test email: No SendGrid API key configured"
}
```

#### Get Percentiles

Calculates and returns the 99th percentile values for each area.

```json
{
  "command": "get_percentiles"
}
```

Returns:

```json
{
  "status": "completed",
  "percentiles": {"A-1": 0.0, "A-2": 45.2, "A-3": 0.0},
  "buffer_sizes": {"A-1": 300, "A-2": 300, "A-3": 300},
  "empty_threshold": 0.0
}
```

#### Clear Buffer

Clears the readings buffer for a specific area or all areas.

```json
{
  "command": "clear_buffer"
}
```

or for a specific area:

```json
{
  "command": "clear_buffer",
  "area": "A-1"
}
```

Returns:

```json
{
  "status": "completed",
  "message": "Cleared all reading buffers"
}
```

or:

```json
{
  "status": "completed",
  "message": "Cleared buffer for area A-1"
}
```

### Sensor Readings

The module provides comprehensive readings that can be used for monitoring:

```json
{
  "empty_areas": ["A-1", "A-3"],
  "location": "389 5th Ave, New York, NY",
  "last_check_time": "2025-04-07 15:35:00",
  "next_check_time": "2025-04-07 15:40:00",
  "total_alerts_sent": 5,
  "last_alert_time": "2025-04-07 15:30:00",
  "weekdays_only": true,
  "check_times": ["08:15", "08:30", "10:15", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30", "14:00", "14:30", "15:00"],
  "areas_monitored": ["A-1", "A-2", "A-3"],
  "include_image": true,
  "empty_threshold": 0.0,
  "sampling_window_minutes": 5,
  "sampling_interval_seconds": 1,
  "last_reading_time": "2025-04-07 15:34:30",
  "last_percentiles": {"A-1": 0.0, "A-2": 45.2, "A-3": 0.0},
  "pid": 12345
}
```

### Implementation Details
* **Advanced Detection**: Uses the 99th percentile of readings over a configurable sampling window to reliably determine if an area is empty.
* **Scheduling**: Checks occur at specific times (check_times) with support for weekday-only operation.
* **State Persistence**: Uses fasteners for inter-process locking and saves state to ~/.stock-alert/*.json files, including last check times, alert history, and image paths.
* **Image Capture**: Supports capturing images from Viam cameras (e.g., ffmpeg) and attaching them to emails if include_image is enabled.
* **Email Alerts**: Uses SendGrid for sending emails with both HTML and plain text versions.
Dependency Handling: Robustly manages dependencies like sensors and cameras.

### Known Issues and Debugging

* **Email Sending Error**: If you see an error like "Please use a To, From, Cc or Bcc object", this indicates an issue with how email recipients are configured. Ensure that each recipient in the configuration is a valid email string.
* **Image Type Warning**: The warning "Unsupported image type" suggests that the image returned by the camera might not be handled correctly. The module attempts to handle different image formats, but may need to be updated for specific camera implementations.
* **State and Locking**: The state persistence uses file locking with fasteners, which should prevent race conditions, but ensure the lock files (*.lock) are not corrupted or inaccessible.
* **Dependencies**: Verify that the langer_fill sensor and camera (if used) are correctly configured and accessible.

For further debugging, check the SendGrid API key, ensure the camera is returning the expected image format, and review the logs for any error messages.