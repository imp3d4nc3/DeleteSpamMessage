"""
Discordアカウント復旧用クリーンアップスクリプト (iPhone対応版)
====================================================
外部ライブラリ不要(標準ライブラリのみ)で動作するため、
a-Shell や Pyto など iPhone 上の Python アプリでそのまま実行できます。

■ 前提条件(必ず先に実施すること)
  1. パスワードを変更し、Discordアプリ設定 > マイアカウント >
     「すべてのデバイスからログアウト」を実行済みであること
  2. 二要素認証(2FA)を有効化済みであること
  3. 上記実施後に「新しく」取得した自分のユーザートークンを使うこと

■ iPhoneでのトークンの取得方法
  DevToolsが使えないiPhoneでは、Safariの「Webインスペクタ」がPCのSafari/Chromeと
  連携している場合を除き直接は取れません。以下のどちらかで取得してください:
    a) PC(Mac/Windows)のブラウザでDiscordにログインし、開発者ツール(F12)の
       Networkタブでリクエストヘッダの 'authorization' の値をコピーし、
       iPhoneのメモアプリなどに安全に転記して使う(その後メモは削除)
    b) iPad/Macが使えない場合は、iPhoneの「Split View」等は使えないため、
       この自動化は諦めて後述の「アプリで手動削除」を使う

■ iPhoneでの実行手順(a-Shellを使う場合。無料・App Store提供)
  1. App Storeで「a-Shell」をインストール
  2. a-Shellを開き、以下を実行してこのスクリプトを作成:
       vi discord_cleanup_ios.py
     (viで開いたら i キーで挿入モードにし、このファイルの中身を貼り付け、
      Escを押して :wq で保存。貼り付けは長いので、iCloud Drive経由で
      ファイルを転送する方法の方が簡単です。下記「ファイル転送」参照)
  3. 環境変数をセットして実行:
       export DISCORD_TOKEN="新しいトークンをここに"
       python3 discord_cleanup_ios.py --list
       python3 discord_cleanup_ios.py --delete

■ ファイル転送(推奨)
  1. このファイルをiPhoneの「ファイル」アプリのiCloud Drive等に保存
  2. a-Shell アプリ内から「ファイル」アプリ経由でアクセス可能な
     ~/Documents (a-Shellの共有フォルダ) にこのファイルを置く
  3. a-Shellで:
       cd ~/Documents
       export DISCORD_TOKEN="新しいトークン"
       python3 discord_cleanup_ios.py --list

■ 注意
  - ユーザートークンでの自動操作はDiscord利用規約上グレー〜禁止領域です。
    今回のような「本人による不正送信物の復旧削除」目的の一時利用として、
    自己責任で使用してください。使用後はトークンとこのファイルを削除すること。
  - レートリミットを尊重するため削除間隔を空けています。件数が多いと時間がかかります。
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

API_BASE = "https://discord.com/api/v10"


def request(method, url, token, params=None, retries=5):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method=method)
    req.add_header("authorization", token)
    req.add_header("content-type", "application/json")
    req.add_header("user-agent", "Mozilla/5.0")
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read()
                return resp.status, (json.loads(body) if body else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            if e.code == 429:
                try:
                    body = json.loads(raw)
                    retry_after = body.get("retry_after", 1)
                except Exception:
                    retry_after = 2
                time.sleep(retry_after + 0.5)
                continue
            body = raw
            try:
                return e.code, json.loads(body)
            except Exception:
                return e.code, None
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            # 一時的な回線断・タイムアウトはリトライする
            print(f"  [通信エラー、再試行します: {e}]")
            time.sleep(2 + attempt * 2)
            continue
    return None, None


def get_me(token):
    status, data = request("GET", f"{API_BASE}/users/@me", token)
    if status != 200:
        raise RuntimeError(f"トークンが無効です (status={status})。新しいトークンを取得してください。")
    return data


def get_guilds(token):
    status, data = request("GET", f"{API_BASE}/users/@me/guilds", token)
    if status != 200:
        return []
    return data


MAX_SEARCH_OFFSET = 5000  # Discordの検索APIはこれを超えるoffsetを扱えない


def _search_messages(url, token, author_id, label):
    """検索APIの共通ロジック。202(インデックス構築中)やその他一時エラーをリトライする。"""
    results = []
    offset = 0
    consecutive_errors = 0
    while True:
        status, data = request(
            "GET",
            url,
            token,
            params={"author_id": author_id, "offset": offset},
        )
        if status == 202:
            # インデックスがまだ構築中。送信直後のメッセージが検索対象になるまで
            # 数秒〜数十秒かかることがあるため待って再試行する。
            retry_after = 2
            if isinstance(data, dict):
                retry_after = data.get("retry_after", 2)
            print(f"  [{label}] 検索インデックス構築中、{retry_after}秒待って再試行します...")
            time.sleep(retry_after + 0.5)
            continue
        if status in (401, 403, 404, 405):
            # 権限不足/未対応エンドポイントなど、リトライしても解決しない恒久的なエラー。
            # 待たずに即座に諦める。
            print(f"  [{label}] このエンドポイントは利用できません (status={status})。スキップします。")
            break
        if status != 200 or data is None:
            consecutive_errors += 1
            if consecutive_errors > 5:
                print(f"  [{label}] 検索に繰り返し失敗したため打ち切ります (status={status})")
                break
            time.sleep(1.5 * consecutive_errors)
            continue
        consecutive_errors = 0
        messages_group = data.get("messages", [])
        if not messages_group:
            break
        for group in messages_group:
            for msg in group:
                if msg.get("hit"):
                    results.append(msg)
        total = data.get("total_results", 0)
        offset += len(messages_group)
        if offset >= total:
            break
        if offset >= MAX_SEARCH_OFFSET:
            print(f"  [{label}] 検索結果が{MAX_SEARCH_OFFSET}件を超えるため、検索APIの上限で"
                  f"打ち切りました(全{total}件中{offset}件のみ取得)。--start/--endで期間を絞って"
                  f"複数回に分けて実行してください。")
            break
        time.sleep(0.3)
    return results


def search_my_messages_in_guild(token, guild_id, author_id):
    return _search_messages(
        f"{API_BASE}/guilds/{guild_id}/messages/search", token, author_id, f"guild:{guild_id}"
    )


def get_dm_channels(token):
    status, data = request("GET", f"{API_BASE}/users/@me/channels", token)
    if status != 200:
        return []
    return data


def get_friends(token):
    """フレンド(相互フォロー済みの relationship type=1)一覧を取得する。"""
    status, data = request("GET", f"{API_BASE}/users/@me/relationships", token)
    if status != 200 or not data:
        return []
    return [r for r in data if r.get("type") == 1]


def open_dm_channel(token, recipient_id):
    """
    フレンドとのDMチャンネルを取得(未作成なら作成)する。
    /users/@me/channels の一覧には出てこない「一度も開いていない」「閉じた」DMでも、
    このAPIを呼ぶだけでチャンネルIDが得られる(メッセージは送信されない)。
    """
    req = urllib.request.Request(
        f"{API_BASE}/users/@me/channels", method="POST",
        data=json.dumps({"recipient_id": recipient_id}).encode("utf-8"),
    )
    req.add_header("authorization", token)
    req.add_header("content-type", "application/json")
    req.add_header("user-agent", "Mozilla/5.0")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read()
            return json.loads(body) if body else None
    except urllib.error.HTTPError:
        return None
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return None


def search_my_messages_in_channel(token, channel_id, author_id):
    """DMやグループDMなど、ギルドに属さないチャンネル内の自分のメッセージを検索する。"""
    return _search_messages(
        f"{API_BASE}/channels/{channel_id}/messages/search", token, author_id, f"dm:{channel_id}"
    )


def delete_message(token, channel_id, message_id):
    status, _ = request("DELETE", f"{API_BASE}/channels/{channel_id}/messages/{message_id}", token)
    # 404は既に(モデレーター等により)削除済みという意味なので成功扱いにする
    return status in (200, 204, 404)


def list_scheduled_events(token, guild_id):
    status, data = request("GET", f"{API_BASE}/guilds/{guild_id}/scheduled-events", token)
    if status != 200 or not data:
        return []
    return data


def delete_scheduled_event(token, guild_id, event_id):
    status, _ = request("DELETE", f"{API_BASE}/guilds/{guild_id}/scheduled-events/{event_id}", token)
    return status in (200, 204, 404)


def delete_messages_parallel(token, msgs, max_workers=6):
    """
    複数メッセージを並列に削除する。request()側で429(レートリミット)を
    検知して自動的に待機・再試行するため、ここでは固定sleepを入れず
    ワーカー数で全体のスループットを制御する。
    """
    deleted = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_msg = {
            executor.submit(delete_message, token, msg["channel_id"], msg["id"]): msg
            for msg in msgs
        }
        for future in as_completed(future_to_msg):
            msg = future_to_msg[future]
            ok = future.result()
            print(f"  - {'削除済み' if ok else '削除失敗'}: channel={msg['channel_id']} msg={msg['id']}")
            if ok:
                deleted += 1
    return deleted


def delete_events_parallel(token, guild_id, events, max_workers=6):
    deleted = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_ev = {
            executor.submit(delete_scheduled_event, token, guild_id, ev["id"]): ev
            for ev in events
        }
        for future in as_completed(future_to_ev):
            ev = future_to_ev[future]
            ok = future.result()
            print(f"  - {'削除済み' if ok else '削除失敗'}: event={ev['id']} name={ev.get('name', '')!r}")
            if ok:
                deleted += 1
    return deleted


def filter_by_date(messages, start_date, end_date):
    """
    start_date / end_date は 'YYYY-MM-DD' 形式。
    Discordのメッセージtimestampはisoformat('YYYY-MM-DDTHH:MM:SS...')なので
    先頭10文字(日付部分)を文字列比較するだけで範囲判定できる。
    """
    if not start_date and not end_date:
        return messages
    result = []
    for msg in messages:
        ts = msg.get("timestamp", "")[:10]
        if not ts:
            continue
        if start_date and ts < start_date:
            continue
        if end_date and ts > end_date:
            continue
        result.append(msg)
    return result


def run_once(token, args, pass_no, total_passes):
    if total_passes > 1:
        print(f"\n########## パス {pass_no}/{total_passes} ##########")

    me = get_me(token)
    author_id = me["id"]
    print(f"アカウント: {me['username']} (id={author_id})")

    scope = getattr(args, "scope", "all")  # "all" / "guilds" / "dms" / "friends"
    include_guilds = scope in ("all", "guilds")
    include_open_dms = scope in ("all", "dms")
    include_friend_dms = scope in ("all", "dms", "friends")
    scope_label = {
        "all": "サーバー+DM+フレンド",
        "guilds": "サーバーのみ",
        "dms": "DM(開いているもの)+フレンド",
        "friends": "フレンドのみ",
    }.get(scope, scope)
    print(f"対象範囲: {scope_label}")

    total_msgs_found = total_msgs_deleted = 0
    total_events_found = total_events_deleted = 0

    guild_results = {}
    guild_events = {}
    guilds = []
    if include_guilds:
        guilds = get_guilds(token)
        print(f"参加サーバー数: {len(guilds)}")

        print("サーバーを検索中(並列処理)...")
        with ThreadPoolExecutor(max_workers=8) as executor:
            future_to_guild = {
                executor.submit(search_my_messages_in_guild, token, g["id"], author_id): g
                for g in guilds
            }
            event_future_to_guild = {
                executor.submit(list_scheduled_events, token, g["id"]): g for g in guilds
            }
            done = 0
            for future in as_completed(future_to_guild):
                g = future_to_guild[future]
                guild_results[g["id"]] = future.result()
                done += 1
                print(f"  検索完了 {done}/{len(guilds)} サーバー", end="\r")
            print()
            for future in as_completed(event_future_to_guild):
                g = event_future_to_guild[future]
                guild_events[g["id"]] = future.result()

        for guild in guilds:
            gid = guild["id"]
            gname = guild.get("name", gid)

            msgs = filter_by_date(guild_results.get(gid, []), args.start, args.end)
            if msgs:
                print(f"[{gname}] 自分のメッセージ {len(msgs)} 件を検出")
                total_msgs_found += len(msgs)
                if args.list:
                    for msg in msgs:
                        preview = (msg.get("content") or "")[:50]
                        print(f"  - channel={msg['channel_id']} msg={msg['id']} content={preview!r}")
                else:
                    total_msgs_deleted += delete_messages_parallel(token, msgs)

            events = guild_events.get(gid, [])
            my_events = [e for e in events if e.get("creator_id") == author_id]
            if my_events:
                print(f"[{gname}] 自分が作成したイベント {len(my_events)} 件を検出")
                total_events_found += len(my_events)
                if args.list:
                    for ev in my_events:
                        print(f"  - event={ev['id']} name={ev.get('name', '')!r}")
                else:
                    total_events_deleted += delete_events_parallel(token, gid, my_events)

    if include_open_dms or include_friend_dms:
        dm_channels = []
        known_channel_ids = set()

        if include_open_dms:
            dm_channels = get_dm_channels(token)
            known_channel_ids = {ch["id"] for ch in dm_channels}
            print(f"DM/グループDM数(開いているもの): {len(dm_channels)}")

        if include_friend_dms:
            friends = get_friends(token)
            print(f"フレンド数: {len(friends)}")
            if friends:
                print("フレンドのDMチャンネルを確認中(未オープン/閉じたDMも回収)...")
                with ThreadPoolExecutor(max_workers=8) as executor:
                    future_to_friend = {
                        executor.submit(open_dm_channel, token, f["id"]): f for f in friends
                    }
                    opened = 0
                    for future in as_completed(future_to_friend):
                        friend = future_to_friend[future]
                        ch = future.result()
                        opened += 1
                        print(f"  確認完了 {opened}/{len(friends)} フレンド", end="\r")
                        if ch and ch.get("id") and ch["id"] not in known_channel_ids:
                            known_channel_ids.add(ch["id"])
                            dm_channels.append(ch)
                print()
                print(f"DM/グループDM数(合計): {len(dm_channels)}")

        print("DMを検索中(並列処理)...")

        dm_results = {}
        with ThreadPoolExecutor(max_workers=8) as executor:
            future_to_ch = {
                executor.submit(search_my_messages_in_channel, token, ch["id"], author_id): ch
                for ch in dm_channels
            }
            done = 0
            for future in as_completed(future_to_ch):
                ch = future_to_ch[future]
                dm_results[ch["id"]] = future.result()
                done += 1
                print(f"  検索完了 {done}/{len(dm_channels)} DM", end="\r")
            print()

        channel_name_by_id = {}
        for ch in dm_channels:
            cid = ch["id"]
            if ch.get("type") == 1:
                recipients = ch.get("recipients") or []
                channel_name_by_id[cid] = recipients[0].get("username", cid) if recipients else cid
            else:
                channel_name_by_id[cid] = ch.get("name") or f"グループDM({cid})"

        for cid, msgs_raw in dm_results.items():
            cname = channel_name_by_id.get(cid, cid)
            msgs = filter_by_date(msgs_raw, args.start, args.end)
            if msgs:
                print(f"[DM:{cname}] 自分のメッセージ {len(msgs)} 件を検出")
                total_msgs_found += len(msgs)
                if args.list:
                    for msg in msgs:
                        preview = (msg.get("content") or "")[:50]
                        print(f"  - channel={msg['channel_id']} msg={msg['id']} content={preview!r}")
                else:
                    total_msgs_deleted += delete_messages_parallel(token, msgs)

    print("\n===== サマリー =====")
    print(f"検出したメッセージ: {total_msgs_found} 件")
    if args.delete:
        print(f"削除したメッセージ: {total_msgs_deleted} 件")
    print(f"検出したイベント: {total_events_found} 件")
    if args.delete:
        print(f"削除したイベント: {total_events_deleted} 件")

    return total_msgs_found, total_msgs_deleted, total_events_found, total_events_deleted


def main():
    parser = argparse.ArgumentParser(description="Discordアカウント復旧クリーンアップ (iPhone対応版)")
    parser.add_argument("--list", action="store_true", help="削除対象を一覧表示するだけ")
    parser.add_argument("--delete", action="store_true", help="実際に削除する")
    parser.add_argument("--start", help="この日付(YYYY-MM-DD)以降のメッセージのみ対象にする(乗っ取り開始日など)")
    parser.add_argument("--end", help="この日付(YYYY-MM-DD)以前のメッセージのみ対象にする(乗っ取り終了日=対応完了日など)")
    parser.add_argument(
        "--scope", choices=["all", "guilds", "dms", "friends"], default="all",
        help="対象範囲: all=サーバー+DM+フレンド(既定), guilds=サーバーのみ, "
             "dms=開いているDM+フレンド, friends=フレンドのDMのみ",
    )
    parser.add_argument(
        "--passes", type=int, default=1,
        help="検索&削除を何回繰り返すか(既定1)。Discordの検索インデックスは反映まで"
             "ラグがあり、1回目で見つからなかったメッセージが後から検索に出てくることが"
             "あるため、--delete時は2〜3回繰り返すと取りこぼしを減らせます。",
    )
    parser.add_argument(
        "--wait-between-passes", type=int, default=30,
        help="パス間の待機秒数(既定30秒)。検索インデックスの更新を待つため。",
    )
    args = parser.parse_args()

    if not args.list and not args.delete:
        print("使い方: --list で確認、--delete で削除を実行してください。")
        sys.exit(1)

    if args.start or args.end:
        print(f"日付フィルタ: {args.start or '指定なし'} 〜 {args.end or '指定なし'} の範囲のみ対象")
    else:
        print("警告: 日付フィルタが指定されていません。全期間のメッセージが対象になります。")
        print("乗っ取られた期間だけに絞るには --start YYYY-MM-DD --end YYYY-MM-DD を指定してください。")

    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        print("環境変数 DISCORD_TOKEN が設定されていません。")
        sys.exit(1)

    grand_msgs_deleted = 0
    grand_events_deleted = 0
    for pass_no in range(1, args.passes + 1):
        found_m, deleted_m, found_e, deleted_e = run_once(token, args, pass_no, args.passes)
        grand_msgs_deleted += deleted_m
        grand_events_deleted += deleted_e
        is_last = pass_no == args.passes
        if not is_last and args.delete:
            if found_m == 0 and found_e == 0:
                print("このパスでは何も見つかりませんでした。以降のパスは省略します。")
                break
            print(f"次のパスまで{args.wait_between_passes}秒待機します(検索インデックス更新待ち)...")
            time.sleep(args.wait_between_passes)

    if args.passes > 1 and args.delete:
        print("\n===== 全パス合計 =====")
        print(f"削除したメッセージ合計: {grand_msgs_deleted} 件")
        print(f"削除したイベント合計: {grand_events_deleted} 件")


if __name__ == "__main__":
    main()
