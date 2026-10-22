import pytest
from fastapi import HTTPException, Response
from test_bot_log_reader import db
from app.services import forward_results

def test_forward_route_ownership_and_no_store(db,monkeypatch):
    import app.main as main
    monkeypatch.setattr(main,'get_authenticated_user_id',lambda:'a')
    calls=[]
    monkeypatch.setattr(forward_results,'report',lambda *args,**kwargs:calls.append(args) or {})
    with pytest.raises(HTTPException) as error:
        main.get_forward_results(2,Response(),days=7,db=db)
    assert error.value.status_code==404 and not calls
    response=Response()
    main.get_forward_results(1,response,days=7,db=db)
    assert calls==[('a',101,1)]
    assert response.headers['Cache-Control']=='no-store'
