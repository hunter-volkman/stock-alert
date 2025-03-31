# Module stock-alert 

This module monitors stock levels from a `viam-soleng:stock-fill:fillpercent` sensor and sends alerts when specified areas of interest are empty. It’s designed for general use across stores, polling every 15 minutes by default, with configurable alert language.

## Model hunter:stock-alert:email

Sends email alerts via a remote SendGrid service when configured areas of interest are empty (e.g., stock fill level = 0), with a customizable subject line.

### Configuration

```json
{
  "location": "Test Location",
  "recipients": ["recipient1@gmail.com", "recipient2@gmail.com"],
  "areas": ["Area-1", "Area-2", "Area-3"],
  "descriptor": "Boxes"
}
```

#### Attributes

| Name          | Type   | Inclusion | Description                |
|---------------|--------|-----------|----------------------------|
| `location` | string  | Required  | The location. |
| `recipients` | list[str] | Required  | List of email addresses to receive alerts. |
| `areas` | list[str] | Required  | List of specific area identifiers to monitor (e.g., "A-1"). |
| `descriptor` | string | Optional  | Descriptor for areas in alerts (e.g., "Shelves", "Coolers"; defaults to "Areas of Interest"). |

#### Example Configuration

```json
{
  "name": "langer_alert_email",
  "type": "sensor",
  "model": "hunter:stock-alert:email",
  "attributes": {
    "location": "389 5th Ave, New York, NY",
    "recipients": ["hunter.volkman@viam.com", "pret-a-manger@viam.com"],
    "areas": ["A-1", "A-2", "A-3"],
    "descriptor": "Shelves"
  },
  "depends_on": ["langer_fill", "shared-services:sendgrid_email"]
}
```

#### Dependencies
* `langer_fill`: Local sensor (`viam-soleng:stock-fill:fillpercent`).
* `shared-services:sendgrid_email`: Remote service (`mcvella:messaging:sendgrid-email`).

### DoCommand

If your model implements DoCommand, provide an example payload of each command that is supported and the arguments that can be used. If your model does not implement DoCommand, remove this section.

#### Example DoCommand

```json
{
  "command_name": {
    "arg1": "foo",
    "arg2": 1
  }
}
```
