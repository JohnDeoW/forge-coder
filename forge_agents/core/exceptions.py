"""异常体系"""

class ForgeAgentsException(Exception):
    """ForgeAgents基础异常类"""
    pass

class LLMException(ForgeAgentsException):
    """LLM相关异常"""
    pass

class AgentException(ForgeAgentsException):
    """Agent相关异常"""
    pass

class ConfigException(ForgeAgentsException):
    """配置相关异常"""
    pass

class ToolException(ForgeAgentsException):
    """工具相关异常"""
    pass
