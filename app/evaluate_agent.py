"""Opt-in live Groq evaluation. Never run implicitly during tests or startup."""
import argparse
import json
from pathlib import Path
from uuid import uuid4
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.config import settings
from app.db import Base, User, Preference
from app.ingest import ingest
from app.agent import run_agent, GroqClient, AgentError
from app.schemas import AgentAnswer

ROOT=Path(__file__).resolve().parent.parent

class RecordingClient(GroqClient):
    def __init__(self):
        super().__init__(); self.validity=[]
    def complete(self,messages):
        reply=super().complete(messages)
        if not reply.get('tool_calls'):
            try:
                AgentAnswer.model_validate_json(reply.get('content') or '')
                self.validity.append(True)
            except ValueError:
                self.validity.append(False)
        return reply

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--live',action='store_true',help='Send API requests using your Groq quota')
    parser.add_argument('--limit',type=int,default=5)
    args=parser.parse_args()
    if not args.live: parser.error('Pass --live to explicitly enable real Groq requests')
    if not settings().groq_api_key: parser.error('Configure GROQ_API_KEY in .env first')
    if not 1<=args.limit<=30: parser.error('limit must be 1–30')
    queries=json.loads((ROOT/'data/eval_queries.json').read_text())[:args.limit]
    engine=create_engine('sqlite://'); Base.metadata.create_all(engine)
    rows=[]
    with Session(engine) as db:
        ingest(db,json.loads((ROOT/'data/sample_jobs.json').read_text()))
        for q in queries:
            uid=uuid4().hex; db.add(User(id=uid)); db.commit()
            db.add(Preference(user_id=uid,values={'real_only':False})); db.commit()
            client=RecordingClient()
            try:
                answer=run_agent(db,uid,q['query'],client)
                rows.append({'query':q['query'],'success':True,'schema_attempts':client.validity,'returned_job_ids':[j['id'] for j in answer['jobs']]})
            except AgentError as exc:
                db.rollback()
                rows.append({'query':q['query'],'success':False,'schema_attempts':client.validity,'error':exc.message})
                if exc.status in (429,503): break
    attempts=[valid for row in rows for valid in row['schema_attempts']]
    first=[row['schema_attempts'][0] for row in rows if row['schema_attempts']]
    report={'model':settings().groq_model,'requested_queries':len(queries),'completed_queries':len(rows),'successful_grounded_responses':sum(r['success'] for r in rows),'schema_validity_rate':sum(attempts)/len(attempts) if attempts else None,'first_response_schema_validity_rate':sum(first)/len(first) if first else None,'note':'Synthetic corpus; schema/grounding checks do not measure recommendation correctness.','rows':rows}
    path=ROOT/'reports/agent.json'; path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(report,indent=2)+'\n'); print(json.dumps(report,indent=2))

if __name__=='__main__': main()
