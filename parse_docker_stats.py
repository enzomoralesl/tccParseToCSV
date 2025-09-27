import pandas as pd
import re
from io import StringIO
from datetime import datetime

# As colunas são inferidas durante o parsing, mas mantemos o FWF_COLUMNS para referência
FWF_COLUMNS = [
    'CONTAINER ID', 'NAME', 'CPU %', 'MEM USAGE / LIMIT', 'MEM %', 
    'NET I/O', 'BLOCK I/O', 'PIDS'
]


def convert_size_to_mib(val_str):
    """Converte strings de tamanho (KiB, MiB, GiB, B, KB, MB, GB) para MiB (megabytes binários)."""
    if pd.isna(val_str) or not isinstance(val_str, str): return None
    val_str = val_str.strip().upper().replace(',', '.')
    
    if val_str == '0B': return 0.0

    match = re.match(r'([\d.]+)([KMGT]?I?B)', val_str)
    if not match: return None 
    
    value = float(match.group(1))
    unit = match.group(2)
    
    # Conversões: assumindo que o Docker usa MB/GB (base 1000) e MiB/GiB (base 1024)
    # Vamos converter tudo para MiB (base 1024) para consistência em análise de desempenho.
    if unit in ('KIB', 'KB'): return value / 1024
    if unit in ('MIB', 'MB'): return value
    if unit in ('GIB', 'GB'): return value * 1024
    if unit in ('TIB', 'TB'): return value * 1024 * 1024
    if unit == 'B': return value / (1024 * 1024)
    return None


def parse_mem_usage_limit(s):
    """Divide a string 'USAGE / LIMIT' e converte ambas as partes para MiB."""
    if pd.isna(s) or not isinstance(s, str): return None, None
    parts = s.split(' / ')
    if len(parts) != 2: return None, None
    usage = convert_size_to_mib(parts[0].strip())
    limit = convert_size_to_mib(parts[1].strip())
    return usage, limit

def parse_io_tx_rx(s):
    """Divide a string 'RX / TX' ou 'READ / WRITE' e converte ambas as partes para MiB."""
    if pd.isna(s) or not isinstance(s, str): return None, None
    parts = s.split(' / ')
    if len(parts) != 2: return None, None
    # No docker stats, a ordem é RECEBIDO / ENVIADO (Net I/O) ou LIDO / ESCRITO (Block I/O)
    rx_read = convert_size_to_mib(parts[0].strip())
    tx_write = convert_size_to_mib(parts[1].strip())
    return rx_read, tx_write


def parse_docker_stats_log(log_file_path):
    with open(log_file_path, 'r') as f:
        log_content = f.read()

    # --- NOVO PADRÃO DE REGEX PARA O SEU FORMATO DE LOG ---
    # Captura:
    # 1. TIMESTAMP (em linha separada, Grupo 1)
    # 2. BLOCO DE DADOS (iniciando após o cabeçalho, Grupo 2)
    block_pattern = re.compile(
        r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\n(?:CONTAINER ID.*?\n)?(.*?)(?=\n\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}|\n--- END OF SAMPLE)", 
        re.MULTILINE | re.DOTALL
    )

    all_data = []
    
    for match in block_pattern.finditer(log_content):
        timestamp_str = match.group(1).strip()
        stats_block = match.group(2).strip()

        if not stats_block:
            continue
        
        # O cabeçalho CONTAINER ID (e a linha que o segue) agora é tratado no bloco de dados se a regex não o removeu
        lines = stats_block.split('\n')
        
        # Filtra linhas vazias ou de cabeçalho
        lines = [line.strip() for line in lines if line.strip() and not line.strip().startswith('CONTAINER ID')]

        try:
            for line in lines:
                line = line.strip()
                if not line: continue
                
                # 1. Separar CONTAINER ID do resto:
                # Usa match_id.group(1) para o ID e match_id.group(2) para o resto (NAME, CPU%, etc.)
                match_id = re.match(r'(\S+)\s+(.*)', line)
                
                if match_id:
                    container_id = match_id.group(1)
                    rest_of_line = match_id.group(2).strip()
                    
                    # 2. Separar o restante (NAME e os 6 campos restantes)
                    # NAME (tokens[0]) é capturado até 2 ou mais espaços, o que é o delimitador mais comum
                    # para separar o nome das métricas.
                    tokens = re.split(r'\s{2,}', rest_of_line, maxsplit=6)
                    
                    # Esperamos 7 tokens: NAME, CPU%, MEM_USAGE/LIMIT, MEM%, NET_I/O, BLOCK_I/O, PIDS
                    if len(tokens) == 7:
                        
                        container_name = tokens[0]
                        cpu_percent_raw = tokens[1]
                        mem_usage_limit_raw = tokens[2]
                        mem_percent_raw = tokens[3]
                        net_io_raw = tokens[4]
                        block_io_raw = tokens[5]
                        pids_raw = tokens[6]
                        
                        row_dict = {
                            'Timestamp': timestamp_str,
                            'Container_ID': container_id,
                            'Container_Name': container_name,
                            'CPU_Percent_Raw': cpu_percent_raw,
                            'Mem_Usage_Limit_Raw': mem_usage_limit_raw,
                            'Mem_Percent_Raw': mem_percent_raw,
                            'Net_IO_Raw': net_io_raw,
                            'Block_IO_Raw': block_io_raw,
                            'PIDS_Raw': pids_raw
                        }
                        all_data.append(row_dict)
                    else:
                        print(f"Warning: Linha pulada - Split incorreto ({len(tokens)} tokens esperados 7). Conteúdo: {line}")
                else:
                    print(f"Warning: Linha pulada - Falha ao separar ID do resto. Conteúdo: {line}")

                        
        except Exception as e:
            print(f"Erro inesperado ao processar a linha de stats: {line}. Erro: {e}")
            continue

    df = pd.DataFrame(all_data)

    if df.empty:
        return pd.DataFrame()


    def parse_percentage(s):
        """Converte string de porcentagem para float."""
        return float(str(s).replace('%', '').replace(',', '.')) if pd.notna(s) and isinstance(s, str) else None

    # Aplica as funções de parseamento e conversão de unidades para criar colunas numéricas
    df['Timestamp'] = pd.to_datetime(df['Timestamp'])
    df['CPU_Percent'] = df['CPU_Percent_Raw'].apply(parse_percentage)
    df['Mem_Percent'] = df['Mem_Percent_Raw'].apply(parse_percentage)

    # Processa 'Mem_Usage_Limit_Raw' para duas novas colunas numéricas
    df[['Mem_Usage_MiB', 'Mem_Limit_MiB']] = df['Mem_Usage_Limit_Raw'].apply(lambda x: pd.Series(parse_mem_usage_limit(x)))
    
    # Processa Net_IO_Raw e Block_IO_Raw
    df[['Net_Rx_MiB', 'Net_Tx_MiB']] = df['Net_IO_Raw'].apply(lambda x: pd.Series(parse_io_tx_rx(x)))
    df[['Block_Read_MiB', 'Block_Write_MiB']] = df['Block_IO_Raw'].apply(lambda x: pd.Series(parse_io_tx_rx(x)))
    
    # Converte PIDS para numérico
    df['PIDS'] = pd.to_numeric(df['PIDS_Raw'], errors='coerce')


    # Seleciona e reordena as colunas FINAIS (apenas numéricas e de identificação)
    final_cols = [
        'Timestamp', 'Container_ID', 'Container_Name', 'CPU_Percent',
        'Mem_Usage_MiB', 'Mem_Limit_MiB', 'Mem_Percent',
        'Net_Rx_MiB', 'Net_Tx_MiB', 'Block_Read_MiB', 'Block_Write_MiB', 'PIDS'
    ]
    
    # Remove as colunas temporárias '_Raw'
    columns_to_drop = [col for col in df.columns if col not in final_cols]
    df_final = df.drop(columns=columns_to_drop, errors='ignore')

    # Garante a ordem das colunas
    df_final = df_final[final_cols].copy()

    return df_final


if __name__ == "__main__":
    # Nomes dos arquivos de log brutos (você deve criá-los com o script Bash)
    log_file_java = 'docker_stats_raw_java.log'
    log_file_python = 'docker_stats_raw_python.log'
    
    # Tentativa de ler os arquivos de log reais
    try:
        df_parsed_java = parse_docker_stats_log(log_file_java)
        df_parsed_python = parse_docker_stats_log(log_file_python)
    except FileNotFoundError:
        print("Erro: Um ou ambos os arquivos de log não foram encontrados. Certifique-se de que 'docker_stats_raw_java.log' e 'docker_stats_raw_python.log' estão no mesmo diretório do script.")
        exit()
    except Exception as e:
        print(f"Erro fatal durante o parseamento: {e}")
        exit()


    # -----------------------------------------------------
    # SALVAMENTO E GERAÇÃO DE CSV POR GRUPO (AJUSTADO)
    # -----------------------------------------------------

    if not df_parsed_java.empty:
        # Colunas com dados numéricos e nomes descritivos
        df_parsed_java.to_csv('all_docker_stats_parsed_java.csv', index=False)

        for name, group_df in df_parsed_java.groupby('Container_Name'):
            container_alias = name
            if name == 'medapp-app-1':
                container_alias = 'java'
            elif name == 'postgres':
                container_alias = 'jPostgres' # Postgres usado pela app Java
            
            output_csv = f'{container_alias}_parsed_stats.csv'
            group_df.to_csv(output_csv, index=False)
            print(f"Dados para {container_alias} salvos em {output_csv}")
        print("Processamento do log Java concluído. Verifique os arquivos CSV gerados.")
    else:
        print(f"Nenhum dado válido foi parseado do log: {log_file_java}. Verifique se o formato das linhas no log está correto.")


    if not df_parsed_python.empty:
        df_parsed_python.to_csv('all_docker_stats_parsed_python.csv', index=False)

        for name, group_df in df_parsed_python.groupby('Container_Name'):
            container_alias = name
            if name == 'pymedapp':
                container_alias = 'python'
            elif name == 'postgres_db':
                container_alias = 'pPostgres' # Postgres usado pela app Python
                
            output_csv = f'{container_alias}_parsed_stats.csv'
            group_df.to_csv(output_csv, index=False)
            print(f"Dados para {container_alias} salvos em {output_csv}")
        print("Processamento do log Python concluído. Verifique os arquivos CSV gerados.")
    else:
        print(f"Nenhum dado válido foi parseado do log: {log_file_python}. Verifique se o formato das linhas no log está correto.")
