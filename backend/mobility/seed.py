"""Fictional trips and explicitly non-authoritative demonstration policies."""

TRIPS = [
    dict(id="trip-river", date="Oct 20, 2026", time="9:42 PM", destination="River North", pickup="West Loop", vehicle="Standard", status="Canceled", total=650, estimated=0, duration="Canceled before pickup", distance="0 mi", issue="cancellation", arrival_disputed=True, eligibility="review", caption="A pickup that never happened.", color="sand"),
    dict(id="trip-airport", date="Oct 19, 2026", time="7:15 AM", destination="O'Hare Airport", pickup="Lincoln Park", vehicle="Comfort", status="Completed", total=4820, estimated=4120, duration="38 min", distance="17.4 mi", issue="fare", arrival_disputed=False, eligibility="adjust", caption="A fare worth a second look.", color="blue"),
    dict(id="trip-museum", date="Oct 17, 2026", time="2:08 PM", destination="Art Institute", pickup="Wicker Park", vehicle="Standard", status="Completed", total=1875, estimated=1875, duration="21 min", distance="5.2 mi", issue="payment", arrival_disputed=False, eligibility="explain", caption="Your receipt, explained.", color="green"),
]

POLICIES = {
    "cancellation": dict(id="demo-cancellation-v1", title="Cancellation fee review", version="Demo policy · v1", region="Chicago", text="Conflicting pickup accounts require human review. A GPS arrival event alone does not establish that the rider could find the vehicle. No automatic adjustment is permitted while this conflict is unresolved."),
    "fare": dict(id="demo-fare-v1", title="Fare difference review", version="Demo policy · v1", region="Chicago", text="In this fictional scenario, an unsupported fare difference is eligible for an adjustment equal to the recorded excess. The rider must confirm before the simulated adjustment is recorded."),
    "payment": dict(id="demo-payment-v1", title="Payment authorization", version="Demo policy · v1", region="Chicago", text="A pending authorization and a settled charge are separate records. A matching pending authorization is not evidence of a second settled payment. A rider can request human review if their bank statement differs."),
}
