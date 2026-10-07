#!/bin/sh
set -eu

base_url=${BASE_URL:-http://127.0.0.1:8000}
smoke_dir=$(mktemp -d)
trap 'rm -rf "$smoke_dir"' EXIT INT TERM
last_body=
last_status=

request() {
    label=$1
    method=$2
    path=$3
    expected=$4
    body=${5-}
    key=${6-}
    last_body="$smoke_dir/$label.json"
    if [ -n "$key" ]; then
        last_status=$(curl --silent --show-error --output "$last_body" --write-out '%{http_code}' \
            --request "$method" "$base_url$path" --header 'Content-Type: application/json' \
            --header "Idempotency-Key: $key" --data "$body")
    elif [ -n "$body" ]; then
        last_status=$(curl --silent --show-error --output "$last_body" --write-out '%{http_code}' \
            --request "$method" "$base_url$path" --header 'Content-Type: application/json' \
            --data "$body")
    else
        last_status=$(curl --silent --show-error --output "$last_body" --write-out '%{http_code}' \
            --request "$method" "$base_url$path")
    fi
    case " $expected " in
        *" $last_status "*) ;;
        *)
            echo "$label returned HTTP $last_status; expected $expected" >&2
            cat "$last_body" >&2
            exit 1
            ;;
    esac
    echo "$label: HTTP $last_status"
}

json_value() {
    python3 - "$1" "$2" <<'PY'
import json
import sys

value = json.load(open(sys.argv[1]))
for part in sys.argv[2].split("."):
    value = value[int(part)] if isinstance(value, list) else value[part]
print(value)
PY
}

request live GET /health/live 200
[ "$(json_value "$last_body" status)" = live ]
request ready GET /health/ready 200
[ "$(json_value "$last_body" status)" = ready ]

unique=$(python3 -c 'import uuid; print(uuid.uuid4().hex)')
request customer-create POST /api/v1/customers 201 \
    "{\"name\":\"API Smoke\",\"email\":\"smoke-$unique@example.test\"}"
customer_id=$(json_value "$last_body" id)
request customer-get GET "/api/v1/customers/$customer_id" 200

request products-list GET '/api/v1/products?page=1&page_size=100' 200
product_id=$(python3 - "$last_body" <<'PY'
import json
import sys

products = json.load(open(sys.argv[1]))["results"]
available = next((row for row in products if row["available_quantity"] > 0), None)
if available is None:
    raise SystemExit("No seeded product has available inventory; reset or replenish local data.")
print(available["id"])
PY
)
request product-get GET "/api/v1/products/$product_id" 200

request active-cart-missing GET "/api/v1/customers/$customer_id/cart" 404
[ "$(json_value "$last_body" error.code)" = cart_not_found ]
request active-cart-create PUT "/api/v1/customers/$customer_id/cart" 201 '{}'
cart_id=$(json_value "$last_body" id)
request active-cart-existing PUT "/api/v1/customers/$customer_id/cart" 200 '{}'
request active-cart-get GET "/api/v1/customers/$customer_id/cart" 200

add_key="smoke-add-$unique"
item_body="{\"product_id\":\"$product_id\",\"quantity\":1}"
request item-add POST "/api/v1/customers/$customer_id/cart/items" 201 "$item_body" "$add_key"
request item-add-replay POST "/api/v1/customers/$customer_id/cart/items" 200 "$item_body" "$add_key"
request cart-get GET "/api/v1/carts/$cart_id" 200
request item-update PATCH "/api/v1/carts/$cart_id/items/$product_id" 200 '{"quantity":1}'
request item-delete DELETE "/api/v1/carts/$cart_id/items/$product_id" 204
request item-delete-repeat DELETE "/api/v1/carts/$cart_id/items/$product_id" 204
request item-readd POST "/api/v1/customers/$customer_id/cart/items" 201 "$item_body" \
    "smoke-readd-$unique"

checkout_key="smoke-checkout-$unique"
request checkout POST "/api/v1/carts/$cart_id/checkout" 202 '{}' "$checkout_key"
order_id=$(json_value "$last_body" id)
request checkout-replay POST "/api/v1/carts/$cart_id/checkout" '200 202' '{}' "$checkout_key"
[ "$(json_value "$last_body" id)" = "$order_id" ]
request order-pending GET "/api/v1/orders/$order_id" 200

docker compose run --rm worker python manage.py run_worker --once >/dev/null
attempt=0
while [ "$attempt" -lt 20 ]; do
    request order-final GET "/api/v1/orders/$order_id" 200
    order_status=$(json_value "$last_body" status)
    [ "$order_status" != PENDING ] && break
    attempt=$((attempt + 1))
    sleep 1
done
[ "$order_status" = CONFIRMED ] || {
    echo "Order $order_id did not confirm; final state: $order_status" >&2
    exit 1
}

request coupon-generate POST /api/v1/admin/coupons/generate '200 201 409' '{}' \
    "smoke-coupon-$unique"
if [ "$last_status" = 409 ]; then
    [ "$(json_value "$last_body" error.code)" = no_coupon_eligible ]
fi
request coupons-list GET '/api/v1/admin/coupons?page=1&page_size=100' 200
request report GET /api/v1/admin/reports/summary 200
python3 - "$last_body" <<'PY'
import json
import sys

report = json.load(open(sys.argv[1]))
assert report["gross_revenue_cents"] - report["discounts_cents"] == report["net_revenue_cents"]
coupons = report["coupons"]
assert coupons["generated"] == coupons["available"] + coupons["reserved"] + coupons["redeemed"]
PY

echo "All API smoke checks passed. Customer: $customer_id Order: $order_id"
