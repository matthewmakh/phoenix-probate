"""Check database results after backfill."""
from dotenv import load_dotenv
load_dotenv()

from database import get_database, ProbateRecord
from sqlalchemy import func

db = get_database()
session = db.Session()

# Total records
total = session.query(ProbateRecord).count()

# Records with relationship
with_rel = session.query(ProbateRecord).filter(
    ProbateRecord.executor_relationship.isnot(None),
    ProbateRecord.executor_relationship != ''
).count()

# Records with estate value
with_val = session.query(ProbateRecord).filter(
    ProbateRecord.estimated_estate_value.isnot(None),
    ProbateRecord.estimated_estate_value != ''
).count()

print('DATABASE SUMMARY')
print('=' * 50)
print(f'Total records:              {total}')
print(f'With relationship:          {with_rel}')
print(f'With estate value:          {with_val}')
print()

# Show breakdown of relationships
print('RELATIONSHIP BREAKDOWN:')
rels = session.query(
    ProbateRecord.executor_relationship,
    func.count(ProbateRecord.id)
).filter(
    ProbateRecord.executor_relationship.isnot(None),
    ProbateRecord.executor_relationship != ''
).group_by(ProbateRecord.executor_relationship).all()

for rel, count in sorted(rels, key=lambda x: -x[1])[:20]:
    print(f'   {rel}: {count}')

print()
print('SAMPLE ESTATE VALUES:')
vals = session.query(
    ProbateRecord.file_number, 
    ProbateRecord.decedent_name, 
    ProbateRecord.estimated_estate_value
).filter(
    ProbateRecord.estimated_estate_value.isnot(None),
    ProbateRecord.estimated_estate_value != '',
    ProbateRecord.estimated_estate_value != 'null'
).limit(15).all()

for fn, name, val in vals:
    name_display = (name or '')[:25].ljust(25)
    print(f'   {fn}: {name_display} ${val}')

session.close()
