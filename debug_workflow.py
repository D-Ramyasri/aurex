from fastapi.testclient import TestClient
from main import app
from incidents import create_incident
from canonical_incident import generate_incident_id

client = TestClient(app)
client.post('/sim/reset')
client.post('/sim/inject', json={'scenario':'bad_deploy'})
print('before', client.get('/sim/checkout-service/metrics').json())
inc = create_incident(
    incident_id=generate_incident_id(),
    alert='Bad deploy on checkout-service',
    extracted_details={'service':'checkout-service'},
    diagnosis='Null pointer in v2.4.1',
    reflection='Rollback to v2.4.0',
    raw_recalled_memories=[],
    recommended_action={'type':'rollback_deployment', 'target_service':'checkout-service', 'params':{}, 'rationale':'revert bad deploy'}
)
inc_id = inc['id']
client.post(f'/incidents/{inc_id}/approve', json={'approver':'SRE','note':'Rollback approved'})
r = client.post(f'/incidents/{inc_id}/execute')
print('status_code', r.status_code)
print(r.json())
print('final incident', client.get(f'/incidents/{inc_id}').json())

client.post('/sim/reset')
client.post('/sim/inject', json={'scenario':'db_pool_exhaustion'})
inc2 = create_incident(
    incident_id=generate_incident_id(),
    alert='PostgreSQL connections maxed out',
    extracted_details={'service':'database-cluster'},
    diagnosis='Exhaustion',
    reflection='Restart clears connections temporarily',
    raw_recalled_memories=[],
    recommended_action={'type':'restart_service', 'target_service':'database-cluster', 'params':{}, 'rationale':'partial relief'}
)
inc2_id = inc2['id']
client.post(f'/incidents/{inc2_id}/approve', json={'approver':'SRE','note':'Restart'})
r2 = client.post(f'/incidents/{inc2_id}/execute')
print('status2', r2.status_code)
print(r2.json())
print('final incident2', client.get(f'/incidents/{inc2_id}').json())
