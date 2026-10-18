"""Versioned, normalized human annotations. AI observations are never ground truth."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .geometry import validate_geometry

OBJECTS = {'person':'Человек','safe':'Сейф','safe_door':'Дверца сейфа','cash_drawer':'Денежный ящик','room_door':'Дверь помещения','cash':'Деньги / пачка','table':'Стол / стойка','chair':'Стул','transfer_tray':'Лоток передачи','banknote_detector':'Детектор банкнот','money_counter':'Счётчик банкнот','phone':'Телефон','bag':'Сумка','document':'Документ','key':'Ключ'}
ZONES = {'cashier':'Рабочее место кассира','customer':'Место клиента','cash_surface':'Поверхность для денег','transfer':'Зона передачи','entrance':'Вход / дверь','storage':'Хранение','other':'Другая зона'}
EVENTS = {'cash_pickup':'Взять деньги','cash_place':'Положить деньги','cash_transfer':'Передать деньги','cash_count':'Считать / сортировать','cash_check':'Проверить банкноты','safe_open':'Открыть сейф','safe_close':'Закрыть сейф','door_open':'Открыть дверь','door_close':'Закрыть дверь','zone_enter':'Войти в зону','zone_exit':'Выйти из зоны','phone_use':'Использовать телефон','document_transfer':'Передать документ','safe_unattended':'Открытый сейф без человека','cash_unattended':'Деньги без человека','door_unattended':'Открытая дверь без человека','other':'Другое наблюдение'}
STATES = {'open':'Открыто','closed':'Закрыто','person_present':'Человек есть','person_absent':'Человека нет (вся зона видна)','cash_present':'Деньги есть','cash_absent':'Денег нет (вся зона видна)','unknown':'Не видно / неизвестно'}
# Quick episode protocol: who did what with which item, without drawing boxes.
ROLES = {'cashier':'Кассир','customer':'Клиент','collector':'Инкассатор','other':'Другой человек','unknown':'Роль неясна'}
ACTIONS = {'hand_over':'Передать из рук в руки','put_down':'Положить (на стол, в лоток)','pick_up':'Взять (со стола, из лотка)','count':'Пересчитать (руками или машиной)','check':'Проверить (документ, купюры, сумку)','store':'Убрать в сейф / ящик','take_out':'Достать из сейфа / ящика','open':'Открыть','close':'Закрыть','lock':'Запереть ключом','arrive':'Войти в помещение / подойти к месту клиента','enter':'Зайти на сторону кассира (за его рабочий стол)','leave':'Уйти с рабочего места','phone':'Пользоваться телефоном','other':'Другое'}
ITEMS = {'cash':'Деньги','document':'Документ','card':'Карта','key':'Ключ','bag':'Сумка / инкассаторская сумка','safe':'Сейф','door':'Дверь','phone':'Телефон','personal':'Личная вещь','none':'Без предмета','unknown':'Не разобрать'}
VISIBILITY = {'clear':'Хорошо видно','partial':'Видно частично','hidden':'Не видно, вывод по контексту'}
LABEL_CHECKS = {'unset':'Не проверено','explained':'Нашёл эпизод, который объясняет метку','partly':'Объясняет частично / есть сомнения','not_found':'Не нашёл, что объясняет метку','disagree':'Не согласен с меткой'}
class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
class Keyframe(Model):
    time: float = Field(ge=0)
    box: list[float] = Field(min_length=4,max_length=4)
    polygons: list[list[list[float]]] = Field(default_factory=list,max_length=30)
    mask_source: str = Field(default='',max_length=80)
    @model_validator(mode='after')
    def valid(self):
        x,y,w,h=self.box
        if min(x,y,w,h)<0 or w<=0 or h<=0 or x+w>1.000001 or y+h>1.000001: raise ValueError('Рамка должна находиться внутри кадра')
        for polygon in self.polygons:
            if not 3<=len(polygon)<=1000 or any(len(p)!=2 or any(v<0 or v>1 for v in p) for p in polygon):raise ValueError('Некорректный полигон маски')
        return self
class Obj(Model):
    id: str = Field(min_length=1,max_length=80)
    label: str
    name: str = Field(default='',max_length=200)
    role: Literal['unknown','cashier','customer','collector']='unknown'
    zone_id: str = ''
    keyframes: list[Keyframe] = Field(default_factory=list,max_length=10000)
    interpolate: bool = False
    @model_validator(mode='after')
    def valid(self):
        if self.label not in OBJECTS: raise ValueError('Неизвестный класс объекта')
        times=[k.time for k in self.keyframes]
        if len(times)!=len(set(times)): raise ValueError('Повтор времени ключевого кадра')
        return self
class Zone(Model):
    id: str = Field(min_length=1,max_length=80)
    label: str
    name: str = Field(default='',max_length=200)
    points: list[list[float]] = Field(min_length=3,max_length=100)
    anchors: list[str] = Field(default_factory=list,max_length=100)
    confirmed: bool = True
    @model_validator(mode='after')
    def valid(self):
        if self.label not in ZONES: raise ValueError('Неизвестный класс зоны')
        if any(len(p)!=2 or any(v<0 or v>1 for v in p) for p in self.points): raise ValueError('Зона должна находиться внутри кадра')
        area=abs(sum(self.points[i][0]*self.points[(i+1)%len(self.points)][1]-self.points[(i+1)%len(self.points)][0]*self.points[i][1] for i in range(len(self.points))))/2
        if area<0.00001: raise ValueError('Площадь зоны слишком мала')
        return self
class Interval(Model):
    id: str = Field(min_length=1,max_length=80)
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    note: str = Field(default='',max_length=4000)
    @model_validator(mode='after')
    def ordered(self):
        if self.end<self.start: raise ValueError('Конец интервала раньше начала')
        return self
class State(Interval):
    target_id: str
    value: str
    fully_visible: bool=False
    @model_validator(mode='after')
    def valid(self):
        if self.value not in STATES: raise ValueError('Неизвестное состояние')
        if self.value.endswith('_absent') and not self.fully_visible: raise ValueError('Отсутствие можно подтвердить только при полной видимости зоны')
        return self
class Event(Interval):
    label: str
    actor_id: str=''
    target_id: str=''
    zone_id: str=''
    provenance: Literal['human','ai_suggestion','rule_candidate']='human'
    @model_validator(mode='after')
    def valid(self):
        if self.label not in EVENTS: raise ValueError('Неизвестный класс события')
        return self
class Participant(Model):
    id: str = Field(min_length=1,max_length=80)
    name: str = Field(min_length=1,max_length=60)
    role: str='unknown'
    @model_validator(mode='after')
    def valid(self):
        if self.role not in ROLES: raise ValueError('Неизвестная роль участника')
        return self
class Episode(Interval):
    action: str
    actor_id: str=''
    recipient_id: str=''
    item: str='unknown'
    visibility: str='clear'
    explains_label: bool=False
    hard_negative: bool=False
    hypothesis: str=Field(default='',max_length=300)
    @model_validator(mode='after')
    def valid(self):
        if self.action not in ACTIONS: raise ValueError('Неизвестное действие эпизода')
        if self.item not in ITEMS: raise ValueError('Неизвестный предмет эпизода')
        if self.visibility not in VISIBILITY: raise ValueError('Неизвестная видимость эпизода')
        if self.explains_label and self.hard_negative: raise ValueError('Эпизод не может одновременно объяснять метку и быть нормальным похожим случаем')
        if self.actor_id and self.actor_id==self.recipient_id: raise ValueError('«Кто» и «кому» — разные участники')
        return self
class LabelCheck(Model):
    status: str='unset'
    text: str=Field(default='',max_length=2000)
    @model_validator(mode='after')
    def valid(self):
        if self.status not in LABEL_CHECKS: raise ValueError('Неизвестный статус проверки метки')
        return self
class Thresholds(Model):
    safe: float=Field(default=15,ge=0.1,le=3600)
    cash: float=Field(default=20,ge=0.1,le=3600)
    door: float=Field(default=30,ge=0.1,le=3600)
class Annotation(Model):
    schema_version: Literal[1]=1
    status: Literal['unreviewed','in_progress','reviewed']='unreviewed'
    reviewer: str=Field(default='',max_length=200)
    verdict: Literal['unset','no_violation','possible_violation','uncertain']='unset'
    full_video_reviewed: bool=False
    notes: str=Field(default='',max_length=16000)
    scene_id: str=Field(default='',max_length=120)
    objects: list[Obj]=Field(default_factory=list,max_length=1000)
    zones: list[Zone]=Field(default_factory=list,max_length=100)
    states: list[State]=Field(default_factory=list,max_length=10000)
    events: list[Event]=Field(default_factory=list,max_length=10000)
    thresholds: Thresholds=Field(default_factory=Thresholds)
    participants: list[Participant]=Field(default_factory=list,max_length=30)
    episodes: list[Episode]=Field(default_factory=list,max_length=1000)
    label_check: LabelCheck=Field(default_factory=LabelCheck)
    @model_validator(mode='after')
    def references(self):
        ids=[x.id for seq in (self.objects,self.zones,self.states,self.events,self.participants,self.episodes) for x in seq]
        if len(ids)!=len(set(ids)): raise ValueError('ID должны быть уникальны')
        people={p.id for p in self.participants}
        for e in self.episodes:
            if any(i and i not in people for i in (e.actor_id,e.recipient_id)): raise ValueError('Эпизод ссылается на неизвестного участника')
        objects={x.id:x for x in self.objects}; zones={x.id for x in self.zones}; targets=set(objects)|zones
        for o in self.objects:
            if o.zone_id and o.zone_id not in zones: raise ValueError('Неизвестная зона объекта')
        for z in self.zones:
            if any(i not in objects for i in z.anchors): raise ValueError('Неизвестный опорный объект зоны')
        for s in self.states:
            if s.target_id not in targets: raise ValueError('Неизвестная цель состояния')
            if s.value.startswith('person_') and s.target_id not in zones: raise ValueError('Присутствие человека отмечается для зоны')
            if s.value in ('open','closed') and (s.target_id not in objects or objects[s.target_id].label not in ('safe','safe_door','cash_drawer','room_door')): raise ValueError('Открыто/закрыто относится к сейфу, ящику или двери')
        for e in self.events:
            if e.actor_id and (e.actor_id not in objects or objects[e.actor_id].label!='person'): raise ValueError('Участник должен быть человеком')
            if e.target_id and e.target_id not in targets: raise ValueError('Неизвестная цель события')
            if e.zone_id and e.zone_id not in zones: raise ValueError('Неизвестная зона события')
        if self.status=='reviewed' and (not self.reviewer.strip() or self.verdict=='unset' or not self.full_video_reviewed): raise ValueError('Укажите проверяющего, вывод и подтвердите просмотр целиком')
        if self.status=='reviewed' and any(not z.confirmed for z in self.zones): raise ValueError('Подтвердите или удалите предложенные зоны')
        return self
class SaveRequest(Model):
    revision: int=Field(ge=0)
    annotation: Annotation
    @model_validator(mode='after')
    def valid_geometry(self):
        validate_geometry(self.annotation.model_dump())
        return self
