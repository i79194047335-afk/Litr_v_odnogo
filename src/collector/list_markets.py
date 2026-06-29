"""Print Lighter perp markets with their numeric market_id. Run once to find IDs."""
import asyncio, lighter


async def main():
    client = lighter.ApiClient(
        configuration=lighter.Configuration(host="https://mainnet.zklighter.elliot.ai")
    )
    try:
        resp = await lighter.OrderApi(client).order_books()
        for ob in resp.order_books:
            # field names vary across SDK versions; print whole object compactly
            print(ob)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
