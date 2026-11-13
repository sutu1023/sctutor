import pytest
from test_learning import learning_data as learning_data

from sctutor.config import UserError
from sctutor.knowledge import Review


async def test_knowledge_schema_and_caption_evidence(store, learning_data):
    k, concepts, _ = learning_data
    page = concepts['Copilot']['pages'][0]
    for raw in [
        {'concepts':[{'name':'unknown','page_ids':[page], 'extra':'instruction'}]},
        {'concepts':[{'name':'unknown','page_ids':[page], 'aliases':[' ']}]},
        {'concepts':[], 'relations':[{'prerequisite':'unknown','target':'another','page_ids':[page]}]},
    ]:
        with pytest.raises(UserError):
            k.add_extraction(raw,[page])
    store.execute("INSERT INTO page_assets VALUES (?,?,'【机器生成图片说明】diagram evidence','key')", (page,'/not/served'))
    assert 'diagram evidence' in k.sources([page])[0]['text']
    with pytest.raises(UserError):
        k.review('arbitrary_table','anything',Review(status='approved'))
