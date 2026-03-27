path "kv/data/study-agents/*" {
  capabilities = ["read"]
}

path "kv/metadata/study-agents/*" {
  capabilities = ["read", "list"]
}
