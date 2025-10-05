import asyncio
import os
import platform
from datetime import datetime, timedelta
from typing import Optional

import dotenv
import httpx
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from passlib.hash import phpass
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlmodel import (
    Field,
    Session,
    SQLModel,
    engine,
    create_engine,
    select,
    update,
    delete,
)
from sqlmodel.ext.asyncio.session import AsyncSession

dotenv.load_dotenv()
WP_URI = (
    f'mysql+pymysql://{os.environ["WP_MYSQL_USER"]}'
    f':{os.environ["WP_MYSQL_PASS"]}'
    f'@{os.environ["WP_MYSQL_HOST"]}'
    f':{os.environ["WP_MYSQL_PORT"]}'
    f'/{os.environ["WP_MYSQL_DB_NAME"]}'
)
PG_URI = (
    f'postgresql+asyncpg://{os.environ["PG_USER"]}'
    f':{os.environ["PG_PASSWORD"]}'
    f'@{os.environ["PG_HOST"]}'
    f'/{os.environ["PG_DB_NAME"]}'
)


def get_wp_mysql_engine() -> engine:
    return create_engine(url=WP_URI, echo=False)


def get_pg_engine() -> AsyncEngine:
    return create_async_engine(url=PG_URI, echo=False)


wp_engine = get_wp_mysql_engine()  # Connects to WordPress MySQL
pg_engine = get_pg_engine()  # Connects to dedicated PostgresSQL


def get_my_ip():
    with httpx.Client() as client:
        response = client.get("https://api.ipify.org", timeout=5)
        response = client.get("https://api.ipify.org", timeout=5)
    if response.status_code == 200:
        return response.text
    return "0.0.0.0"


class WPUsers(SQLModel, table=True):
    __tablename__: str = "wp_users"
    ID: int = Field(primary_key=True)
    user_login: str = Field(index=True)
    user_pass: str
    user_nicename: str = Field(index=True)
    user_email: str = Field(index=True)
    user_url: str
    user_registered: datetime
    user_activation_key: str
    user_status: int
    display_name: str


class WPWCAMApiActivation(SQLModel, table=True):
    __tablename__: str = "wp_wc_am_api_activation"
    activation_id: int = Field(primary_key=True, index=True)
    activation_time: datetime
    api_key: str = Field(index=True)
    api_resource_id: int
    assigned_product_id: int
    associated_api_key_id: int
    instance: str = Field(index=True)
    ip_address: str
    master_api_key: str = Field(index=True)
    object: str
    order_id: int
    order_item_id: int
    product_id: str
    product_order_api_key: str
    sub_id: int
    sub_item_id: int
    sub_parent_id: int
    version: str
    update_requests: int
    user_id: int = Field(index=True)


class WPWCAMApiResource(SQLModel, table=True):
    __tablename__: str = "wp_wc_am_api_resource"
    api_resource_id: int = Field(primary_key=True, index=True)
    activation_ids: str
    activations_total: int
    activations_purchased: int
    activations_purchased_total: int
    active: int
    access_expires: datetime
    access_granted: datetime
    associated_api_key_ids: str
    collaborators: str
    download_requests: int
    item_qty: int
    master_api_key: str = Field(index=True)
    order_id: int = Field(index=True)
    order_item_id: int
    order_key: str = Field(index=True)
    parent_id: int
    product_id: int = Field(index=True)
    product_order_api_key: str = Field(index=True)
    product_title: str
    refund_qty: int
    sub_id: int
    sub_item_id: int
    sub_previous_order_id: int
    sub_order_key: str
    sub_parent_id: int
    user_id: int = Field(index=True)
    variation_id: int


class UserSession(SQLModel, table=True):
    __tablename__: str = "user_sessions"
    id: Optional[int] = Field(default=None, primary_key=True)
    token: str
    username: str
    user_id: int
    product_id: int
    master_api_key: str
    this_session: str = Field(index=True)
    ip: Optional[str] = Field(default="0.0.0.0")
    create_date: Optional[datetime] = Field(default=datetime.now())
    last_access: Optional[datetime] = Field(default=datetime.now(), index=True)


class TokenData(SQLModel):
    token: str
    id: int
    email: str
    nicename: str
    firstName: str
    lastName: str
    displayName: str


class LicenseResponse(SQLModel):
    success: bool
    message: str
    total_activations: int
    activations_remaining: int


class Logs(SQLModel, table=True):
    __tablename__: str = "logs"
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str
    ip: str
    message: str
    create_date: Optional[datetime] = Field(default=datetime.now(), index=True)

    def __init__(self, username: str, ip: str, message: str):
        self.username = username
        self.ip = ip
        self.message = message
        self.create_date = datetime.now()
        asyncio.create_task(logging(self))


async def logging(log: Logs):
    async with AsyncSession(pg_engine) as session:
        session.add(log)
        await session.commit()


def get_wp_user_data(username: str) -> WPUsers:
    with Session(wp_engine) as session:
        # If user enters email as username then use email to find user
        if "@" in username and "." in username:
            statement = select(WPUsers).where(WPUsers.user_email == username)
        else:
            statement = select(WPUsers).where(WPUsers.user_login == username)
        results = session.exec(statement).first()
        return results


async def get_token_data(username: str, password: str) -> dict | None:
    auth_endpoint = "https://swadbot.com/wp-json/jwt-auth/v1/token"
    payload = {"username": username, "password": password}
    async with httpx.AsyncClient() as client:
        response = await client.post(auth_endpoint, json=payload, timeout=10)
    if response.status_code == 200:
        return response.json()["data"]
    elif response.status_code == 403:
        print(f'Error 403: {response.json()["message"]}')
        return None
    else:
        print(f'Error: {response.json()["message"]}')
        return None


def verify_pw_hash(pw: str, pw_hash: str) -> bool:
    return phpass.verify(pw, pw_hash)


app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static", html=True), name="static")


@app.on_event("startup")
async def startup():
    asyncio.create_task(deactivate_expired_sessions())


@app.get("/admin/{password}", response_class=HTMLResponse)
async def admin(request: Request, password: str):
    if password == "password":
        active_users = await get_active_users()
        logs = await get_logs_from_db(100)
        return templates.TemplateResponse(
            "admin.html",
            {"request": request, "active_users": active_users, "logs": logs},
        )
    else:
        return JSONResponse(status_code=401, content={"message": "Login failed"})


@app.get("/")
async def root(request: Request):
    try:
        client_ip = request.headers["X-Forwarded-For"]
    except KeyError:
        client_ip = request.client.host
    return {"message": "Welcome sensei!", "ip": client_ip}


@app.post("/login", response_model=TokenData)
async def login(
    request: Request, user_login: str, user_pass: str
) -> dict | JSONResponse:
    data = get_wp_user_data(user_login)
    try:
        client_ip = request.headers["X-Forwarded-For"]
    except KeyError:
        client_ip = request.client.host
    try:
        verified = verify_pw_hash(user_pass, data.user_pass)
    except AttributeError as e:
        print(f"CAUGHT ATTRIBUTE ERROR: {e}")
        verified = False
    if verified:
        token_data = await get_token_data(user_login, user_pass)
        Logs(username=user_login, ip=client_ip, message="Login successful!")
    else:
        Logs(username=user_login, ip=client_ip, message="Login failed!")
        return JSONResponse(status_code=401, content={"message": "Login failed"})
    return token_data


@app.post("/license/{action}")
async def license_api(
    action: str,
    username: str,
    client_id: int,
    token: str,
    session_id: str,
    client_ip: str,
) -> LicenseResponse | None:
    # Possible values for action: 'status', 'activate', 'deactivate'
    if action == "activate":
        if not await check_last_create_date(client_id):
            Logs(
                username=username,
                ip=client_ip,
                message="Activation failed! Rate-limit exceeded.",
            )
            return LicenseResponse(
                success=False,
                message="Rate-limit exceeded. Slow down, sensei.",
                total_activations=0,
                activations_remaining=0,
            )
    url = "https://swadbot.com/"
    api_data = await get_wp_api_resource_data(client_id)
    # print(f'API_DATA: {api_data}')
    try:
        prod_id = api_data.product_id
    except KeyError:
        Logs(
            username=username,
            ip=client_ip,
            message=f"Attemted action {action} failed! No active license found for this user.",
        )
        return LicenseResponse(
            success=False,
            message="No active license found for this user.",
            total_activations=0,
            activations_remaining=0,
        )
    client_session = UserSession(
        token=token,
        username=username,
        user_id=client_id,
        product_id=prod_id,
        master_api_key=api_data.master_api_key,
        this_session=session_id,
        ip=client_ip,
        create_date=datetime.now(),
        last_access=datetime.now(),
    )
    
    if action == "activate":
        await client_session_write(client_session)
    elif action == "status":
        await client_session_update(client_session)
    elif action == "deactivate":
        await client_session_delete(client_session)

    params = {
        "wc-api": "wc-am-api",
        "wc_am_action": action,
        "api_key": api_data.master_api_key,
        "instance": session_id,
        "product_id": api_data.product_id,
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url=url,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if response.status_code != 200:
            Logs(
                username=username,
                ip=client_ip,
                message=f"API {action} failed! Error {response.status_code}",
            )
            return None
        json_data = response.json()
        # print(f'JSON_DATA: {json_data}')

    ar = LicenseResponse(
        success=True, message="", total_activations=0, activations_remaining=0
    )
    if action == "activate":
        ar.success = json_data["success"]
        if "message" in json_data:
            ar.message = json_data["message"]
        elif "error" in json_data:
            ar.message = json_data["error"]
        if json_data["success"]:
            ar.total_activations = json_data["data"]["total_activations"]
            ar.activations_remaining = json_data["data"]["activations_remaining"]
        else:
            ar.total_activations = 0
            ar.activations_remaining = 0
    elif action == "status":
        ar.success = json_data["data"]["activated"]
        ar.message = json_data["status_check"]
        ar.total_activations = json_data["data"]["total_activations"]
        ar.activations_remaining = json_data["data"]["activations_remaining"]
    if action != "status":
        Logs(username=username, ip=client_ip, message=f"API {action} successful!")
    return ar


async def deactivate_expired_sessions() -> None:
    while True:
        async with AsyncSession(pg_engine) as session:
            statement = select(UserSession).where(
                UserSession.last_access < datetime.now() - timedelta(seconds=50)
            )
            results = await session.execute(statement)
            for client_session in results.scalars():
                await license_api(
                    action="deactivate",
                    username=client_session.username,
                    client_id=client_session.user_id,
                    token=client_session.token,
                    session_id=client_session.this_session,
                    client_ip=client_session.ip,
                )
                print(f"DEACTIVATED SESSION: {client_session}")
                await client_session_delete(client_session)
        await asyncio.sleep(10)


async def client_session_delete(client_session: UserSession) -> None:
    async with AsyncSession(pg_engine) as session:
        statement = delete(UserSession).where(
            UserSession.this_session == client_session.this_session
        )
        await session.execute(statement)
        await session.commit()
    # print(f'DELETED SESSION FROM DB: {client_session}')


async def client_session_update(client_session: UserSession) -> None:
    async with AsyncSession(pg_engine) as session:
        statement = (
            update(UserSession)
            .where(UserSession.this_session == client_session.this_session)
            .values(last_access=datetime.now())
        )
        await session.execute(statement)
        await session.commit()
    # print(f'UPDATED SESSION IN DB: {client_session}')


async def client_session_write(client_session: UserSession) -> None:
    async with AsyncSession(pg_engine) as session:
        session.add(client_session)
        await session.commit()
        await session.refresh(client_session)
    # print(f'WROTE SESSION TO DB: {client_session}')


async def get_wp_api_resource_data(user_id: int) -> WPWCAMApiResource:
    with Session(wp_engine) as session:
        statement = select(WPWCAMApiResource).where(
            WPWCAMApiResource.user_id == user_id
        )
        results = session.exec(statement).first()
    # print(f'F_RESULTS: {results}')
    return results


async def get_wp_api_activations_data(
    activation_ids: list[int],
) -> list[WPWCAMApiActivation]:
    with Session(wp_engine) as session:
        total_results = []
        for client_id in activation_ids:
            statement = select(WPWCAMApiActivation).where(
                WPWCAMApiActivation.activation_id == client_id
            )
            results = session.exec(statement).first()
            total_results.append(results)
    # print(f'TOTAL_RESULTS: {total_results}')
    return total_results


async def check_last_create_date(client_id: int) -> bool:
    async with AsyncSession(pg_engine) as session:
        statement = (
            select(UserSession)
            .where(UserSession.user_id == client_id)
            .where(UserSession.create_date > datetime.now() - timedelta(seconds=5))
        )
        results = await session.execute(statement)
        for _ in results.scalars():
            return False
    return True


async def get_active_users() -> list[UserSession]:
    async with AsyncSession(pg_engine) as session:
        statement = select(UserSession)
        results = await session.execute(statement)
        final_list = []
        for client_session in results.scalars():
            final_list.append(client_session)
        return final_list


async def get_logs_from_db(limit: int) -> list[Logs]:
    async with AsyncSession(pg_engine) as session:
        statement = select(Logs).order_by(Logs.create_date).limit(limit)
        results = await session.execute(statement)
        final_list = []
        for log in results.scalars():
            final_list.append(log)
        return final_list


def run():
    if platform.system() == "Windows":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


if __name__ != "__main__":
    # NOT EQUALS!
    run()
