ui = true
disable_mlock = true

listener "tcp" {
  address         = "0.0.0.0:8200"
  tls_cert_file   = "/tls/vault.crt"
  tls_key_file    = "/tls/vault.key"
  tls_min_version = "tls12"
}

storage "raft" {
  path    = "/vault/data"
  node_id = "vault-1"
}

api_addr     = "https://vault:8200"
cluster_addr = "https://vault:8201"
