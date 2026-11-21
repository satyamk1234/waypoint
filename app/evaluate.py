"""Retrieval evaluation against explicitly authored synthetic relevance labels."""
import argparse
import json
import math
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.db import Base
from app.ingest import ingest
from app.search import search
from app.schemas import Preferences
from app.embeddings import model_id

ROOT=Path(__file__).resolve().parent.parent

def metrics(ids,relevance,k):
    relevant={key for key,value in relevance.items() if value>0}
    recall=len(set(ids[:k])&relevant)/max(len(relevant),1)
    def dcg(grades): return sum((2**grade-1)/math.log2(i+2) for i,grade in enumerate(grades))
    actual=dcg([relevance.get(jid,0) for jid in ids[:k]])
    ideal=dcg(sorted(relevance.values(),reverse=True)[:k])
    return {'recall':recall,'ndcg':actual/ideal if ideal else 0}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--jobs',type=Path,default=ROOT/'data/sample_jobs.json')
    parser.add_argument('--queries',type=Path,default=ROOT/'data/eval_queries.json')
    parser.add_argument('--output',type=Path,default=ROOT/'reports/retrieval.json')
    parser.add_argument('-k',type=int,default=5)
    args=parser.parse_args()
    if not 1<=args.k<=20: parser.error('k must be 1–20')
    queries=json.loads(args.queries.read_text())
    # Isolated index avoids mixing user data or live jobs into reproducible evals.
    engine=create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        ingest(db,json.loads(args.jobs.read_text()))
        report={'embedding_model':model_id(),'queries':len(queries),'k':args.k,'dataset':str(args.jobs),'note':'Default fixtures are authored synthetic examples, not an independent real-world benchmark. Reranker is a transparent feature-based heuristic.','results':{}}
        for mode in ['bm25','vector','hybrid','rerank']:
            rows=[]
            for item in queries:
                ids=[j['id'] for j in search(db,item['query'],Preferences(real_only=False),limit=args.k,mode=mode)]
                rows.append(metrics(ids,item['relevance'],args.k))
            report['results'][mode]={key:round(sum(r[key] for r in rows)/max(len(rows),1),4) for key in ['recall','ndcg']}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__': main()
