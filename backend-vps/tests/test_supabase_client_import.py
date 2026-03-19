import sys
import types

from study_agents import supabase_client


def test_load_supabase_symbols_recovers_from_shadowed_module(monkeypatch):
    supabase_client._load_supabase_symbols.cache_clear()
    monkeypatch.setitem(sys.modules, "supabase", types.ModuleType("supabase"))
    try:
        client_cls, options_cls, create_client = supabase_client._load_supabase_symbols()
    finally:
        supabase_client._load_supabase_symbols.cache_clear()

    assert client_cls.__name__ == "Client"
    assert options_cls.__name__
    assert callable(create_client)
