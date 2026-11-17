"""Device feature commands. Reading history never claims desktop session verification."""
from .core import GuardError

class DeviceFeatures:
    OPS={'sessions','reply','select_session','send','edit','action','dismiss_notification'}
    def __init__(self,controller,history):self.ctl=controller;self.history=history;self.selected=None;self.last_data={}
    def command(self,msg):
        ctl=self.ctl;op=msg['op']
        if ctl.state in ('recording','processing') and op not in ('dismiss_notification',):
            return dict(**ctl.status(),service_error='BUSY')
        try:
            if op=='sessions':
                app=msg.get('app','codex')
                data=self.history.list_page(msg.get('offset',0)) if app=='codex' else dict(items=[],unsupported=True,reason='暂不支持会话读取',app=app)
                self.last_data=data;return dict(**ctl.status(),data_type='sessions',data=data)
            if op in ('reply','select_session'):
                identifier=msg.get('session_id');data=self.history.reply_page(identifier,msg.get('offset',0))
                self.selected=self.history.get(identifier)
                if op=='select_session':data['input_error']='DESKTOP_SESSION_SWITCH_UNAVAILABLE'
                self.last_data=data;return dict(**ctl.status(),data_type='reply',data=data)
            if op=='send':
                if ctl.input_mode=='ai':
                    if not ctl.target:
                        raise GuardError('SESSION_UNVERIFIED')
                    if (msg.get('scope')=='specific_session' or ctl.target.get('scope')!='application_current') and (not self.selected or ctl.target.get('session_id')!=self.selected['id']):
                        raise GuardError('SESSION_UNVERIFIED')
                    if not ctl.desktop.check(ctl.target):raise GuardError('FOCUS_LOST')
                    # Basic mode verifies the current composer; advanced mode verifies its ID too.
                    return self._key(msg,ctl.target,'enter')
                if not ctl.target or not ctl.desktop.check(ctl.target):raise GuardError('FOCUS_LOST')
                return self._key(msg,ctl.target,'enter')
            if op=='edit':return dict(**ctl.status(),service_notice='请在电脑端编辑文字')
            if op=='action':
                key=msg.get('key')
                if key not in ('copy','paste','refresh','escape'):raise GuardError('KEY_NOT_ALLOWED')
                if ctl.input_mode=='ai':
                    if not ctl.target or not ctl.desktop.check(ctl.target):raise GuardError('SESSION_UNVERIFIED')
                    target=ctl.target
                else:target=ctl.desktop.capture_current()
                return self._key(msg,target,key)
            if op=='dismiss_notification':self.history.dismiss();return ctl.status()
        except Exception as e:
            return dict(**ctl.status(),service_error=str(e)[:120] if isinstance(e,GuardError) else 'SESSION_READ_UNAVAILABLE')
    def _key(self,msg,target,key):
        action=msg.get('action_id')
        if not isinstance(action,str) or not 1<=len(action)<=80:raise GuardError('ACTION_ID_REQUIRED')
        if action in self.ctl.enter_actions:return dict(**self.ctl.status(),service_notice='重复按键已忽略')
        if len(self.ctl.enter_actions)>=4096:raise GuardError('KEY_LIMIT_RECONNECT_AGENT')
        self.ctl.enter_actions.add(action)
        self.ctl.desktop.focused_key(target,key)
        if key=='enter':return dict(**self.ctl.status(),service_notice='已按回车；由应用决定换行或发送')
        return self.ctl.status()
