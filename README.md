# Module stock-alert 

This module monitors stock levels from a `viam-soleng:stock-fill:fillpercent` sensor and sends alerts when specified areas of interest are empty. It's designed for general use across stores, with configurable operating hours and scheduled checks at specific intervals (e.g., every 15 minutes).

## Model hunter:stock-alert:email

Sends email alerts via a SendGrid service when configured areas of interest are empty (e.g., stock fill level = 0), with a customizable subject line and operating schedule.

### Configuration

```json
{
  "location": "389 5th Ave, New York, NY",
  "recipients": ["hunter.volkman@viam.com", "manager@store.com"],
  "areas": ["A-1", "A-2", "A-3"],
  "descriptor": "Shelves",
  "start_time": "07:00",
  "end_time": "19:00",
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
| `start_time` | string | Optional  | Start time of monitoring in HH format (24-hour). Default: "07:00". |
| `end_time` | string | Optional  | End time of monitoring in HH format (24-hour). Default: "19:00". |
| `interval_minutes` | string | Optional  | How often to check for empty areas in minutes. Default: 15. |


#### Example Configuration

```json
{
  "name": "langer_alert_email",
  "type": "sensor",
  "model": "hunter:stock-alert:email",
  "attributes": {
    "location": "389 5th Ave, New York, NY",
    "recipients": ["hunter.volkman@viam.com", "manager@store.com"],
    "areas": ["A-1", "A-2", "A-3"],
    "descriptor": "Shelves",
    "start_time": "07:00",
    "end_time": "19:00",
    "interval_minutes": 15
  },
  "depends_on": ["langer_fill", "sendgrid_email"]
}
```

#### Dependencies
* `langer_fill`: Component (`viam-soleng:stock-fill:fillpercent`).
* `sendgrid_email`: Service (`mcvella:messaging:sendgrid-email`).


#### Usage
1. Configured the `email` model with your location, recipients, areas, descriptor, and operating schedule.
2. Set up the `langer_fill` sensor and a `sendgrid_email` service.
3. The module checks at specified intervals during operating hours and sends alerts for empty areas (e.g., "389 5th Ave, New York, NY - Empty Shelves: A-1, A-2").

### DoCommand

The email alert model supports the following commands:

#### Check Now

Forces an immediate check for empty areas regardless of schedule.

```json
{
  "command": "check_now"
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

### Update Schedule

Updates operating hours and check interval without requiring full reconfiguration/

```json
{
  "command": "update_schedule",
  "start_time": "08:00",
  "end_time": "20:00",
  "interval_minutes": 30
}
```

## Model hunter:stock-alert:sms

Sends SMS alerts via a Twilio service when configured areas of interest are empty, with a customizable message and operating schedule.

### Configuration

```json
{
  "location": "389 5th Ave, New York, NY",
  "phone_numbers": ["+19175551234", "+12125557890"],
  "areas": ["A-1", "A-2", "A-3"],
  "descriptor": "Shelves",
  "start_time": "07:00",
  "end_time": "19:00",
  "interval_minutes": 15
}
```

#### Attributes

| Name          | Type   | Inclusion | Description                |
|---------------|--------|-----------|----------------------------|
| `location` | string  | Required  | The physical location being monitored. |
| `recipients` | list[str] | Required  | List of phone numbers to receive SMS alerts. |
| `areas` | list[str] | Required  | List of specific area identifiers to monitor (e.g., "A-1"). |
| `descriptor` | string | Optional  | Descriptor for areas in alerts (e.g., "Shelves"). Default: "Areas of Interest". |
| `start_time` | string | Optional  | Start time of monitoring in HH format (24-hour). Default: "07:00". |
| `end_time` | string | Optional  | End time of monitoring in HH format (24-hour). Default: "19:00". |
| `interval_minutes` | string | Optional  | How often to check for empty areas in minutes. Default: 15. |


#### Example Configuration

```json
{
  "name": "langer_alert_sms",
  "type": "sensor",
  "model": "hunter:stock-alert:sms",
  "attributes": {
    "location": "389 5th Ave, New York, NY",
    "phone_numbers": ["+19175551234", "+12125557890"],
    "areas": ["A-1", "A-2", "A-3"],
    "descriptor": "Shelves",
    "start_time": "07:00",
    "end_time": "19:00",
    "interval_minutes": 15
  },
  "depends_on": ["langer_fill", "twilio_sms"]
}
```

#### Dependencies
* `langer_fill`: Component (`viam-soleng:stock-fill:fillpercent`).
* `twilio_sms`: Service (`mcvella:messaging:twilio-sms`).


### Sensor Readings

The module provides comprehensive readings that can be used for monitoring:

```json
{
  "empty_areas": ["A-1", "A-3"],
  "status": "active",
  "location": "389 5th Ave, New York, NY",
  "last_check_time": "2025-04-01 18:45:00",
  "next_scheduled_check": "2025-04-01 19:00:00",
  "total_alerts_sent": 5,
  "last_alert_time": "2025-04-01 18:45:00",
  "within_operating_hours": true,
  "operating_hours": "07:00 to 19:00",
  "interval_minutes": 15,
  "areas_monitored": ["A-1", "A-2", "A-3"]
}
```

### Known Issues

* The module requires the `langer_fill` sensor to be properly configured and accessible/
* During initial startup, the module may log "langer_fill not available yet" until all dependencies are fully initialized.