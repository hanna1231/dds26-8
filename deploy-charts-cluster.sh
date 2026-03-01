#!/usr/bin/env bash
set -euo pipefail

helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo update

# Per-domain Redis Clusters (3 primary + 3 replica each)
helm install -f helm-config/order-redis-cluster-values.yaml \
  order-redis-cluster bitnami/redis-cluster

helm install -f helm-config/stock-redis-cluster-values.yaml \
  stock-redis-cluster bitnami/redis-cluster

helm install -f helm-config/payment-redis-cluster-values.yaml \
  payment-redis-cluster bitnami/redis-cluster

# Ingress controller
helm install -f helm-config/nginx-helm-values.yaml \
  nginx ingress-nginx/ingress-nginx
