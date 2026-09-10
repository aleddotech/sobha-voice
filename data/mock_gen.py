import json
import random
import datetime
import uuid

ISSUE_TYPES = [
    "Plumbing", "Electrical", "AC/HVAC", "Pest Control", "Cleaning",
    "Elevator", "Parking", "Security", "Landscaping", "Swimming Pool",
    "Internet/Telecom", "Intercom", "Water Heater", "Door/Lock", "General Maintenance"
]

PRIORITIES = ["High", "Medium", "Low"]
STATUSES = ["Open", "In Progress", "Resolved", "Closed"]
ASSIGNED_TO = [
    "Team A - Plumbing", "Team B - Electrical", "Team C - HVAC",
    "Team D - General", "Team E - Pest Control", "Team F - Security",
    "Unassigned"
]

RESIDENTS = [
    ("Ahmed Al Farsi", "+971501234567", "Villa 12, Phase 1"),
    ("Sarah Johnson", "+971509876543", "Apt 204, Sobha Hartland Tower A"),
    ("Rajesh Kumar", "+971551234567", "Apt 518, Sobha Creek Vistas"),
    ("Fatima Al Mazrouei", "+971561234567", "Villa 34, Phase 2"),
    ("David Chen", "+971571234567", "Apt 1102, Sobha One Tower B"),
    ("Maria Santos", "+971581234567", "Apt 305, Sobha Hartland Tower B"),
    ("Omar Abdullah", "+971521234567", "Villa 8, Phase 3"),
    ("Priya Nair", "+971551098765", "Apt 720, Sobha Creek Vistas"),
    ("James Wilson", "+971504567890", "Apt 404, Sobha One Tower A"),
    ("Aisha Khalid", "+971565432109", "Villa 21, Phase 1"),
    ("Li Wei", "+971572345678", "Apt 1501, Sobha Hartland Tower A"),
    ("Noor Al Rashid", "+971583456789", "Villa 45, Phase 2"),
    ("Michael Brown", "+971514567890", "Apt 809, Sobha Creek Vistas"),
    ("Deepa Menon", "+971525678901", "Apt 612, Sobha One Tower C"),
    ("Carlos Rivera", "+971536789012", "Villa 17, Phase 3"),
    ("Hana Yamamoto", "+971547890123", "Apt 910, Sobha Hartland Tower B"),
    ("Khalid Al Suwaidi", "+971558901234", "Villa 3, Phase 1"),
    ("Amelia Turner", "+971569012345", "Apt 1205, Sobha One Tower B"),
    ("Arjun Sharma", "+971570123456", "Apt 215, Sobha Creek Vistas"),
    ("Layla Hassan", "+971581234560", "Villa 29, Phase 2"),
]

DESCRIPTIONS = {
    "Plumbing": ["Leaking pipe under kitchen sink", "Bathroom faucet dripping constantly", "Toilet not flushing properly", "Low water pressure throughout unit"],
    "Electrical": ["Power outlet not working in living room", "Circuit breaker tripping repeatedly", "Lights flickering in bedroom", "No power in master bathroom"],
    "AC/HVAC": ["AC not cooling properly", "Strange noise from AC unit", "AC remote not responding", "Water leaking from AC unit"],
    "Pest Control": ["Cockroach infestation in kitchen", "Ants trail near pantry", "Mosquito issue on balcony", "Mouse sighting in unit"],
    "Cleaning": ["Common area needs deep cleaning", "Pool area dirty", "Gym equipment needs sanitizing", "Lobby requires maintenance cleaning"],
    "Elevator": ["Elevator door not closing properly", "Elevator making unusual sounds", "Elevator button stuck on floor 3", "Elevator taking too long to arrive"],
    "Parking": ["Parking spot blocked by another vehicle", "Parking gate not working", "Unauthorized vehicle in my spot", "Parking light out in basement"],
    "Security": ["Security camera not working near entrance", "Access card not working", "Suspicious activity reported", "Main entrance gate malfunction"],
    "Landscaping": ["Garden needs trimming", "Tree branches hanging over balcony", "Irrigation system not working", "Dead plants in common area"],
    "Swimming Pool": ["Pool water appears cloudy", "Pool pump making noise", "Missing pool equipment", "Pool temperature too cold"],
    "Internet/Telecom": ["Internet connection down", "Slow internet speed", "Router not working", "TV cable not connecting"],
    "Intercom": ["Intercom not receiving calls", "Intercom display broken", "Cannot buzz in visitors", "Intercom static/noise"],
    "Water Heater": ["No hot water", "Water heater leaking", "Water taking too long to heat", "Strange smell from hot water"],
    "Door/Lock": ["Unit door lock jammed", "Main door squeaking loudly", "Lock cylinder needs replacement", "Door not closing properly"],
    "General Maintenance": ["Wall paint chipping in living room", "Ceiling crack noticed", "Balcony railing loose", "Window not sealing properly"],
}

tickets = []
base_date = datetime.datetime(2026, 1, 1)

for i in range(50):
    resident = random.choice(RESIDENTS)
    issue_type = random.choice(ISSUE_TYPES)
    priority = random.choices(PRIORITIES, weights=[0.2, 0.5, 0.3])[0]
    status = random.choices(STATUSES, weights=[0.35, 0.3, 0.25, 0.1])[0]

    created_offset = random.randint(0, 240)
    created_at = base_date + datetime.timedelta(days=created_offset)
    updated_at = created_at + datetime.timedelta(days=random.randint(0, 10))

    if status in ["Resolved", "Closed"]:
        assigned = random.choice([t for t in ASSIGNED_TO if t != "Unassigned"])
    elif status == "In Progress":
        assigned = random.choice([t for t in ASSIGNED_TO if t != "Unassigned"])
    else:
        assigned = random.choices(ASSIGNED_TO, weights=[1,1,1,1,1,1,3])[0]

    notes = ""
    if status == "In Progress":
        notes = "Technician dispatched. Work in progress."
    elif status == "Resolved":
        notes = "Issue resolved. Resident notified."
    elif status == "Closed":
        notes = "Closed after resident confirmation."

    ticket = {
        "ticket_id": f"TK-{str(i+1).zfill(4)}",
        "unit_number": resident[2],
        "resident_name": resident[0],
        "contact_number": resident[1],
        "issue_type": issue_type,
        "priority": priority,
        "status": status,
        "description": random.choice(DESCRIPTIONS[issue_type]),
        "assigned_to": assigned,
        "created_at": created_at.strftime("%Y-%m-%dT%H:%M:%S"),
        "updated_at": updated_at.strftime("%Y-%m-%dT%H:%M:%S"),
        "notes": notes,
    }
    tickets.append(ticket)

with open("/Users/aoxo/vscode/sobha/data/db.json", "w") as f:
    json.dump(tickets, f, indent=2)

print(f"Generated {len(tickets)} tickets")
for t in tickets[:3]:
    print(json.dumps(t, indent=2))
