class ServiceBase:
    manager = None

    def __init__(self):
        self.records = []

    def create(self, **kwargs):
        if self.manager is None:
            raise NotImplementedError("Subclasses must define a manager.")  
        else:
            record = self.manager.create(**kwargs)
            self.records.append(record)
        return record
    
    def get(self, **kwargs):
        if self.manager is None:
            raise NotImplementedError("Subclasses must define a manager.")
        else:
            results = self.manager.get(**kwargs)
        return results
    
    def filter(self, **kwargs):
        if self.manager is None:
            raise NotImplementedError("Subclasses must define a manager.")  
        else:
          results =  self.manager.filter(**kwargs)
        return results
    
    def update(self, record, **kwargs):
        if self.manager is None:
            raise NotImplementedError("Subclasses must define a manager.")  
        else:
            for key, value in kwargs.items():
                setattr(record, key, value)
                saved = record.save()
        return saved      
    

    def delete(self, record):
        if self.manager is None:
            raise NotImplementedError("Subclasses must define a manager.")
        else:
            record.delete()
            self.records.remove(record) 
        return "record deleted successfully"    

    def list(self):
        if self.manager is None:
            raise NotImplementedError("Subclasses must define a manager.")
        else:
            records = self.manager.all()
        return records   
    
    
    