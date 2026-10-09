"""Named, explicit saves. No camera frame or game event writes to this library."""
import json
import os
from pathlib import Path
import time
import uuid

class SavedGames:
    def __init__(self, directory, legacy=None):
        self.directory=Path(directory)
        self.legacy=Path(legacy) if legacy else None

    def list(self):
        result=[]
        for path in self.directory.glob('*.json'):
            try:
                item=json.loads(path.read_text())
                result.append(dict(id=path.stem,name=item['name'],saved_at=item['saved_at'],
                                   moves=len(item['game']['moves']),mode=item['game'].get('mode','standard')))
            except (OSError,ValueError,KeyError,TypeError):continue
        if self.legacy and self.legacy.exists():
            result.append(dict(id='previous',name='Previous saved game',saved_at=self.legacy.stat().st_mtime,moves=None,mode='standard'))
        return sorted(result,key=lambda item:item['saved_at'],reverse=True)

    def save(self, game, name=None):
        if name is not None and (not isinstance(name,str) or len(name)>80):
            raise ValueError('Use a name with at most 80 characters.')
        data=game.serialize()
        self.directory.mkdir(parents=True,exist_ok=True)
        identity=str(uuid.uuid4())
        record=dict(name=(name or '').strip() or 'Saved game',saved_at=time.time(),game=data)
        temporary=self.directory/(identity+'.tmp')
        temporary.write_text(json.dumps(record))
        os.replace(temporary,self.directory/(identity+'.json'))
        return identity

    def path_for(self, identity):
        if identity=='previous' and self.legacy:
            path=self.legacy
        else:
            try:
                if str(uuid.UUID(identity))!=identity:raise ValueError()
            except (ValueError,TypeError,AttributeError):
                raise ValueError('Choose a saved game from the list.')
            path=self.directory/(identity+'.json')
        return path

    def delete(self, identity):
        path=self.path_for(identity)
        try:
            path.unlink()
        except FileNotFoundError:
            raise ValueError('This saved game no longer exists.')

    def load(self, identity):
        path=self.path_for(identity)
        try:
            data=json.loads(path.read_text())
            return data if identity=='previous' else data['game']
        except (OSError,ValueError,KeyError,TypeError):
            raise ValueError('This saved game cannot be read. Choose another game.')
