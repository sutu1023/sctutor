import pytest
from test_learning import learning_data as learning_data
from test_learning import question_data

from sctutor.config import UserError
from sctutor.quiz import Quiz
from sctutor.store import Store


async def test_all_question_types_and_invalid_evidence(store, learning_data):
    _, cs, model = learning_data
    quiz = Quiz(store,model)
    for kind in ['choice','blank','short','code']:
        ident = quiz.add_question(question_data(cs['Copilot'],kind))['id']
        assert next(q for q in quiz.questions() if q['id']==ident)['status']=='pending'
    with pytest.raises(UserError):
        quiz.add_question({**question_data(cs['Copilot']),'rubric':''})
    with pytest.raises(UserError):
        quiz.add_question({**question_data(cs['Copilot']),'page_ids':cs['Python']['pages']})
    with pytest.raises(UserError):
        quiz.add_question({**question_data(cs['Copilot']),'answer':'not an option'})


async def test_restart_active_quiz_and_one_due_task(store, learning_data):
    _, cs, model = learning_data
    quiz = Quiz(store,model)
    ident=quiz.add_question(question_data(cs['Copilot']))['id']
    quiz.review_question(ident,'approved')
    started=await quiz.start(1)
    restarted=Quiz(Store(store.path.parent),model)
    assert restarted.store.active_quiz()==started['id']
    assert restarted.view(started['id'])['items'][0]['attempts']==0
    with pytest.raises(UserError):
        restarted.finish(started['id'])
    restarted.cancel(started['id'])
    assert not restarted.store.active_quiz()
