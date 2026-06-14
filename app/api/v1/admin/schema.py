from pydantic import BaseModel,Field

class TenantCreateWithUser(BaseModel):
    
    tenant_name:str = Field(...,min_length=3)
    schema_name:str = Field(...,min_length=3)
    host:str = Field(...,min_length=3)
    name:str = Field(...,min_length=3)
    email:str = Field(...,min_length=5)

    