# Module stock-alert 

This module monitors stock levels from a `viam-soleng:stock-fill:fill-percent` sensor and sends alerts when specified areas of interest are empty. It's designed for retail environments with configurable check times and weekday-only operation by default.

## Model hunter:stock-alert:email

Sends email alerts via a SendGrid service when configured areas of interest are empty (e.g., stock fill level = 0), with a customizable subject line.

### Configuration

```json
{
  "location": "389 5th Ave, New York, NY",
  "recipients": ["hunter.volkman@viam.com"],
  "areas": ["A-1", "A-2", "A-3"],
  "descriptor": "Shelves",
  "weekdays_only": true,
  "morning_check_times": ["08:15", "08:30", "10:15", "10:30"],
  "afternoon_start_time": "10:45",
  "afternoon_end_time": "15:00",
  "interval_minutes": 15
}
```

#### Attributes

| Name          | Type   | Inclusion | Description                |
|---------------|--------|-----------|----------------------------|
| `location` | string  | Required  | The physical location being monitored. |
| `recipients` | list[str] | Required  | List of email addresses to receive email alerts. |
| `areas` | list[str] | Required  | List of specific area identifiers to monitor (e.g., "A-1"). |
| `descriptor` | string | Optional  | Descriptor for areas in alerts (e.g., "Shelves"). Default: "Areas of Interest". |
| `weekdays_only` | string | Optional  | Only run checks on weekdays (Mon-Fri). Default: `true`. |
| `morning_check_times` | string | Optional  | Specific morning check times in HH format. Default: ["08:15", "08:30", "10:15", "10:30"]. |
| `afternoon_start_time` | string | Optional  | Start time for afternoon checks. Default: "10:45". |
| `afternoon_end_time` | string | Optional  | End time for afternoon checks. Default: "15:00". |
| `interval_minutes` | string | Optional  | Interval between afternoon checks in minutes. Default: 15. |


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
    "morning_check_times": ["08:15", "08:30", "10:15", "10:30"],
    "afternoon_start_time": "10:45",
    "afternoon_end_time": "15:00",
    "interval_minutes": 15
  },
  "depends_on": ["langer_fill", "sendgrid_email"]
}
```

#### Dependencies
* `langer_fill`: Sensor component (`viam-soleng:stock-fill:fill-percent`).
* `sendgrid_email`: Generic service (`mcvella:messaging:sendgrid-email`).


#### Usage
1. Configure the `email` model with your location, recipients, and areas.
2. Set up the `langer_fill` sensor and a `sendgrid_email` service.
3. The module checks at specified times and sends alerts for empty areas (e.g., "389 5th Ave, NY - Empty Shelves: A-1, A-2").

### DoCommand

The email alert model supports the following commands:

#### Check Now

Forces an immediate check for empty areas regardless of schedule.

```json
{
  "command": "check_now"
}
```

#### Get Today's Schedule

Returns a list of check times for today.

```json
{
  "command": "get_schedule"
}
```

#### Get History

Retrieves alert history for a specified number of days.

```json
{
  "command": "get_history",
  "days": 7
}
```

### Sensor Readings

The module provides comprehensive readings that can be used for monitoring:

```json
{
  "empty_areas": ["A-1", "A-3"],
  "status": "active",
  "location": "389 5th Ave, New York, NY",
  "last_check_time": "2025-04-04 11:30:00",
  "next_scheduled_check": "2025-04-04 11:45:00",
  "total_alerts_sent": 5,
  "last_alert_time": "2025-04-04 10:30:00",
  "weekdays_only": true,
  "today_check_times": ["08:15", "08:30", "10:15", "10:30", "10:45", "11:00", "11:15", "11:30", "11:45", "12:00", "12:15", "12:30", "12:45", "13:00", "13:15", "13:30", "13:45", "14:00", "14:15", "14:30", "14:45", "15:00"],
  "areas_monitored": ["A-1", "A-2", "A-3"],
  "pid": 12345
}
```

### Implementation Details
* **Process Locking:** Uses `fasteners` to ensure only one check loop runs at a time.
* **State Persistence:** Saves state to `/home/hunter.volkman/stock-alert/state_*.json` files.
* **Scheduled Checks:** Runs checks at specific times as configured.
* **Weekday-Only Operation:** By default, only runs checks on weekdays (Monday-Friday).
* **Dependency Handling:** Robustly detects dependencies even with complex resource names.



### Known Issues

* The module requires the `langer_fill` sensor to be properly configured and accessible.
* During initial startup, the module may log "langer_fill not available yet" until all dependencies are fully initialized.