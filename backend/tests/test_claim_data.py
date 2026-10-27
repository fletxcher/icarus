from pymongo import MongoClient

from api.catalog import FILES, SIMULATIONS, THREADS, USERS
from api.claim_data import claim
from tests.conftest import MONGODB_URI
from tests.test_auth_schema import make_user


def test_unowned_documents_go_to_the_account_and_owned_ones_stay(mongo_db_name: str):
    source = MongoClient(MONGODB_URI)
    try:
        db = source[mongo_db_name]
        db[USERS].insert_one(make_user(id="u1", email="ada@example.com").to_mongo())
        db[THREADS].insert_many([{"_id": "old"}, {"_id": "theirs", "user_id": "u2"}])
        db[FILES].insert_one({"_id": "f"})
        db[SIMULATIONS].insert_many([{"_id": "s1"}, {"_id": "s2"}])

        assert claim(db, "Ada@Example.com", dry_run=True) == {THREADS: 1, FILES: 1, SIMULATIONS: 2}
        assert db[THREADS].find_one({"_id": "old"}) == {"_id": "old"}

        assert claim(db, "ada@example.com") == {THREADS: 1, FILES: 1, SIMULATIONS: 2}
        assert db[THREADS].find_one({"_id": "old"})["user_id"] == "u1"
        assert db[THREADS].find_one({"_id": "theirs"})["user_id"] == "u2"
        assert claim(db, "ada@example.com") == {THREADS: 0, FILES: 0, SIMULATIONS: 0}
    finally:
        source.close()


def test_claiming_for_an_unknown_account_fails(mongo_db_name: str):
    source = MongoClient(MONGODB_URI)
    try:
        try:
            claim(source[mongo_db_name], "nobody@example.com")
        except LookupError as error:
            assert "nobody@example.com" in str(error)
        else:
            raise AssertionError("expected LookupError")
    finally:
        source.close()
